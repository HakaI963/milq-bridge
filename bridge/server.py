#!/usr/bin/env python3
"""MilqBridge — OpenAI-compatible server for FreeMilk models.

Real multi-view SPA UI at http://127.0.0.1:11434/  (Overview · Models · Login · Usage · Agent setup).
Login is the actual FreeMilk/Codex device flow executed server-side (verified live).
Any coding agent: OPENAI_BASE_URL=http://127.0.0.1:11434/v1  OPENAI_API_KEY=<bridge key>
"""
import json, mimetypes, os, re, secrets, sys, threading, time, urllib.parse, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import env
from codex_auth import CodexLogin, GoogleLogin, http_json, jwt_claims

DATA_DIR   = os.environ.get("BRIDGE_DATA", os.path.expanduser("~/.milq-bridge"))
STORE      = os.path.join(DATA_DIR, "codex_tokens.json")
BRIDGE_KEY = os.environ.get("BRIDGE_API_KEY", "sk-milq-" + secrets.token_hex(8))
HOST       = os.environ.get("BRIDGE_HOST", "127.0.0.1")
PORT       = int(os.environ.get("BRIDGE_PORT", "11434"))
KEY_FILE   = os.path.join(DATA_DIR, "bridge_key")

if os.environ.get("BRIDGE_API_KEY"):
    BRIDGE_KEY = os.environ["BRIDGE_API_KEY"]
elif os.path.exists(KEY_FILE):
    BRIDGE_KEY = open(KEY_FILE).read().strip()
else:
    os.makedirs(DATA_DIR, exist_ok=True)
    open(KEY_FILE, "w").write(BRIDGE_KEY); os.chmod(KEY_FILE, 0o600)

CODEX = CodexLogin(STORE)
GOOG  = GoogleLogin(CODEX.tokens, PORT)

# ---------------- usage & status ----------------
USAGE = {"requests": 0, "errors": 0, "in_tokens": 0, "out_tokens": 0,
         "by_model": {}, "by_tier": {}, "log": [], "started": time.time()}
ULOCK = threading.Lock()

def note(model, tier, status, tin=0, tout=0, err=None, ms=0):
    with ULOCK:
        USAGE["requests"] += 1
        if status >= 400: USAGE["errors"] += 1
        USAGE["in_tokens"] += tin; USAGE["out_tokens"] += tout
        USAGE["by_model"][model] = USAGE["by_model"].get(model, 0) + 1
        USAGE["by_tier"][tier] = USAGE["by_tier"].get(tier, 0) + 1
        USAGE["log"].insert(0, {"ts": int(time.time()), "model": model, "tier": tier,
                                "status": status, "in": tin, "out": tout, "ms": ms,
                                "err": (err or "")[:180]})
        USAGE["log"] = USAGE["log"][:200]

def authed(h):
    a = h.get("Authorization", "") or ""
    return a[7:].strip() == BRIDGE_KEY if a.lower().startswith("bearer ") else False

# ---------------- codex backend codec ----------------
def chat_to_responses(messages):
    inp = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        if role == "assistant":
            inp.append({"role": "assistant", "content": [{"type": "output_text", "text": content}]})
        else:
            inp.append({"role": "assistant" if role == "system" else role,
                        "content": [{"type": "input_text", "text": content}]})
    return inp

def responses_to_chat(raw, model):
    try:
        j = json.loads(raw)
    except Exception:
        return {"error": {"message": "bad codex response", "raw": raw[:400].decode(errors="replace")}}
    text = ""
    for item in j.get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") in ("output_text", "text"):
                    text += c.get("text", "")
    if not text:
        text = j.get("output_text") or ""
    u = j.get("usage") or {}
    return {"id": j.get("id", "chatcmpl-milq"), "object": "chat.completion",
            "created": int(time.time()), "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                         "finish_reason": j.get("status", "completed")}],
            "usage": {"prompt_tokens": u.get("input_tokens", 0),
                      "completion_tokens": u.get("output_tokens", 0),
                      "total_tokens": u.get("total_tokens", 0)}}

def codex_headers():
    tok = CODEX.tokens.get("access_token")
    h = {"Content-Type": "application/json", "Accept": "application/json",
         "User-Agent": env.cfnetwork_ua()}
    if tok: h["Authorization"] = "Bearer " + tok
    acc = CODEX.tokens.get("chatgpt_account_id")
    if acc: h["ChatGPT-Account-Id"] = acc
    return h

def codex_chat(model, body, want_stream):
    payload = {"model": model, "input": chat_to_responses(body.get("messages", [])),
               "stream": bool(want_stream), "store": False}
    if body.get("temperature") is not None and False:  # codex rejects some fields; keep minimal
        payload["temperature"] = body["temperature"]
    if body.get("max_tokens"): payload["max_output_tokens"] = body["max_tokens"]
    code, raw = http_json("POST", env.CODEX_BACKEND + "/responses", headers=codex_headers(),
                          body=payload, timeout=180)
    return code, raw

def openai_chat(model, body):
    h = {"Content-Type": "application/json", "User-Agent": env.cfnetwork_ua()}
    key = CODEX.tokens.get("openai_api_key") or os.environ.get("OPENAI_API_KEY", "")
    if key: h["Authorization"] = "Bearer " + key
    code, raw = http_json("POST", env.OPENAI_API_BASE + "/chat/completions", headers=h,
                          body=body, timeout=180)
    return code, raw

def milq_chat(model, body, m):
    # premium/hosted models go through FreeMilk's own proxy once a device-attested
    # session is available (see docs/REVERSE.md — App Attest gate).
    h = {"Content-Type": "application/json", "User-Agent": env.cfnetwork_ua()}
    tok = (CODEX.tokens.get("milq_account") or {}).get("access_token")
    if tok: h["Authorization"] = "Bearer " + tok
    base = os.environ.get("MILQ_CHAT_BASE", env.MILQ_BASE)
    code, raw = http_json("POST", base + "/chat/completions", headers=h, body=body, timeout=180)
    return code, raw

# ---------------- HTTP handler ----------------
def sse_codex_to_chat(upstream_iter, model):
    """Translate Codex responses SSE -> OpenAI chat.completion.chunk SSE."""
    yield b"data: " + json.dumps({"id":"chatcmpl-milq","object":"chat.completion.chunk","created":int(time.time()),
                                  "model":model,"choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":None}]}).encode() + b"\n\n"
    for chunk in upstream_iter:
        for line in chunk.split(b"\n"):
            line = line.strip()
            if not line.startswith(b"data:"): continue
            payload = line[5:].strip()
            if payload == b"[DONE]":
                continue
            try:
                ev = json.loads(payload)
            except Exception:
                continue
            t = ev.get("type","")
            if t == "response.output_text.delta":
                d = json.dumps({"object":"chat.completion.chunk","model":model,
                                "choices":[{"index":0,"delta":{"content":ev.get("delta","")},"finish_reason":None}]})
                yield ("data: " + d + "\n\n").encode()
            elif t == "response.completed":
                u = (ev.get("response") or {}).get("usage") or {}
                d = json.dumps({"object":"chat.completion.chunk","model":model,
                                "choices":[{"index":0,"delta":{},"finish_reason":"stop"}],
                                "usage":{"prompt_tokens":u.get("input_tokens",0),
                                         "completion_tokens":u.get("output_tokens",0),
                                         "total_tokens":u.get("total_tokens",0)}})
                yield ("data: " + d + "\n\n").encode()
                yield b"data: [DONE]\n\n"

def stream_url(url, headers, body, on_status):
    import urllib.request
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=300)
    except urllib.error.HTTPError as e:
        yield (e.code, e.read())
        return
    on_status(resp.status)
    yield ("stream", resp)

class H(BaseHTTPRequestHandler):
    server_version = "MilqBridge/1.0"
    def log_message(self, f, *a):
        sys.stderr.write("[bridge] %s\n" % (f % a))

    # helpers
    def send_raw(self, code, raw, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        try: self.wfile.write(raw)
        except BrokenPipeError: pass

    def send_json(self, code, obj): self.send_raw(code, json.dumps(obj, indent=1).encode())

    def body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        if not n: return {}
        try: return json.loads(self.rfile.read(n).decode() or "{}")
        except Exception: return {}

    def form(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(n).decode() if n else ""
        if self.headers.get("Content-Type", "").startswith("application/x-www-form-urlencoded"):
            return dict(urllib.parse.parse_qsl(raw))
        try: return json.loads(raw or "{}")
        except Exception: return {"_raw": raw}

    # GET
    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/index.html", "/ui"):
            html = self.ui_html()
            return self.send_raw(200, html.encode(), "text/html; charset=utf-8")
        if p == "/ui.css":
            return self.send_raw(200, read_asset("ui.css").encode(), "text/css")
        if p == "/ui.js":
            return self.send_raw(200, read_asset("ui.js").encode(), "application/javascript")
        if p in ("/health", "/v1/health"):
            return self.send_json(200, {"ok": True, "service": "milq-bridge",
                                        "codex_signed_in": bool(CODEX.tokens.get("access_token")),
                                        "models": len(env.MODELS)})
        if p == "/v1/models":
            if not authed(self.headers):
                return self.send_json(401, err("missing/invalid API key"))
            tier = (urllib.parse.parse_qs(self.path.split("?")[1] if "?" in self.path else "")
                    .get("tier", [None])[0])
            ms = [m for m in env.MODELS if (not tier or m["tier"] == tier)]
            return self.send_json(200, {"object": "list", "data": [env.oai_model(m) for m in ms]})
        if p.startswith("/v1/models/"):
            if not authed(self.headers): return self.send_json(401, err("invalid key"))
            mid = p[len("/v1/models/"):]
            m = env.model_by_id(mid)
            return self.send_json(200 if m else 404, env.oai_model(m) if m else err("not found"))
        if p == "/local/key":
            ip = self.client_address[0]
            if ip.startswith("127.") or ip.startswith("::1") or os.environ.get("BRIDGE_LAN") == "1":
                return self.send_json(200, {"key": BRIDGE_KEY})
            return self.send_json(403, err("key is not served to remote hosts; pass ?key= in URL"))
        if p == "/login/codex/status":
            return self.send_json(200, CODEX.status())
        if p == "/usage":
            if not authed(self.headers): return self.send_json(401, err("invalid key"))
            u = dict(USAGE); u["log"] = u["log"][:20]; u["uptime_s"] = int(time.time() - USAGE["started"])
            wh = {}
            if CODEX.tokens.get("access_token"):
                c, raw = http_json("GET", env.WHAM_USAGE, headers=codex_headers(), timeout=15)
                if c == 200:
                    try: wh = json.loads(raw)
                    except Exception: wh = {}
            return self.send_json(200, {"local": u, "chatgpt_wham": wh})
        if p == "/status":
            if not authed(self.headers): return self.send_json(401, err("invalid key"))
            return self.send_json(200, {"login": CODEX.status(),
                                        "usage_summary": {k: USAGE[k] for k in
                                          ("requests", "errors", "in_tokens", "out_tokens")},
                                        "key_tail": BRIDGE_KEY[-4:],
                                        "base_url": f"http://{HOST}:{PORT}/v1"})
        if p == "/ads/unlock":
            if not authed(self.headers): return self.send_json(401, err("invalid key"))
            with ULOCK:
                unlocks = CODEX.tokens.setdefault("ad_unlocks", {})
            return self.send_json(200, {"unlocked_models": [k for k, v in unlocks.items() if v > time.time()],
                                        "note": "premium models require SIPS/plan/ads-unlock; POST /ads/unlock then use them"})
        if p == "/google/callback":
            q = urllib.parse.parse_qs(self.path.split("?")[1] if "?" in self.path else "")
            code = (q.get("code") or [""])[0]
            if not code:
                return self.send_json(400, err("no code"))
            ok, detail = GOOG.exchange(code)
            CODEX._save()
            return self.send_json(200, {"google_exchange_ok": ok, "milq": detail})
        return self.send_json(404, err("not found"))

    # POST
    def do_POST(self):
        p = self.path.split("?")[0]
        body = self.body()
        if p in ("/login/codex/start", "/v1/login/codex/start"):
            return self.send_json(200, CODEX.start())
        if p in ("/login/google/start",):
            return self.send_json(200, {"authorize_url": GOOG.authorize_url(),
                                        "callback": f"http://{HOST}:{PORT}/google/callback",
                                        "note": "Google redirects the iOS client to freemilk:/ scheme; after approving in browser, paste the code here: POST /login/google/exchange {code}"})
        if p in ("/login/google/exchange",):
            b = self.form() if self.headers.get("Content-Type", "").startswith("application/x-www") else body
            code = b.get("code", "")
            ok, detail = GOOG.exchange(code)
            CODEX._save()
            return self.send_json(200, {"ok": ok, "milq": detail})
        if p == "/ads/unlock":
            if not authed(self.headers): return self.send_json(401, err("invalid key"))
            model = body.get("model", "")
            m = env.model_by_id(model)
            if not m or m["tier"] != "premium":
                return self.send_json(400, {"error": "only premium models can be ad-unlocked", "model": model})
            exp = time.time() + 3600
            CODEX.tokens.setdefault("ad_unlocks", {})[model] = exp
            CODEX._save()
            return self.send_json(200, {"ok": True, "model": model, "expires_in": 3600,
                                        "note": "rewarded-ad unlock (gravity contract verified; real ad SDK callback needed in production)"})
        if p == "/settings":
            if not authed(self.headers): return self.send_json(401, err("invalid key"))
            for k in ("openai_api_key", "anthropic_api_key", "milq_device_token"):
                if body.get(k) is not None:
                    CODEX.tokens[k] = body[k]
            CODEX._save()
            return self.send_json(200, {"ok": True})

        if p in ("/v1/chat/completions", "/chat/completions"):
            return self.handle_chat(body, stream=bool(body.get("stream")))
        if p in ("/v1/responses", "/responses"):
            model = body.get("model", "gpt-5-codex")
            if not authed(self.headers):
                return self.send_json(401, err("invalid key"))
            code, raw = http_json("POST", env.CODEX_BACKEND + "/responses",
                                  headers=codex_headers(), body=body, timeout=180)
            note(model, "plan", code, ms=0, err=None if code < 400 else raw.decode(errors="replace")[:150])
            return self.send_raw(code or 502, raw)
        return self.send_json(404, err("not found"))

    # ---- chat with routing + auth gate + tier metering ----
    def handle_chat(self, body, stream):
        t0 = time.time()
        if not authed(self.headers):
            note(body.get("model", "?"), "?", 401, err="unauthorized", ms=int((time.time()-t0)*1000))
            return self.send_json(401, err("invalid API key for milq-bridge"))
        model = body.get("model", "gpt-5-codex")
        m = env.model_by_id(model) or {"tier": "external", "backend": "openai", "id": model}
        tier, backend = m["tier"], m.get("backend", "openai")

        # gate premium
        unlocks = CODEX.tokens.get("ad_unlocks", {})
        plan = CODEX.tokens.get("chatgpt_plan_type") or ""
        has_oai = bool(CODEX.tokens.get("openai_api_key") or os.environ.get("OPENAI_API_KEY"))
        if tier == "premium" and unlocks.get(model, 0) < time.time() and not (has_oai or plan):
            note(model, tier, 402, err="premium locked", ms=int((time.time()-t0)*1000))
            return self.send_json(402, {"error": {"message": f"'{model}' is premium: subscribe or POST /ads/unlock", "type": "premium_required"}})
        if tier in ("free", "plan") and not CODEX.tokens.get("access_token") and backend != "openai":
            note(model, tier, 401, err="not signed in", ms=int((time.time()-t0)*1000))
            return self.send_json(401, {"error": {"message": "not signed in — open / and connect ChatGPT (device login)", "type": "login_required"}})

        if backend == "codex":
            if stream:
                payload = {"model": model, "input": chat_to_responses(body.get("messages", [])),
                           "stream": True, "store": False}
                import urllib.request
                req = urllib.request.Request(env.CODEX_BACKEND + "/responses",
                                             data=json.dumps(payload).encode(), method="POST", headers=codex_headers())
                try:
                    resp = urllib.request.urlopen(req, timeout=300)
                except urllib.error.HTTPError as e:
                    raw = e.read(); note(model, tier, e.code, err=raw.decode(errors='replace')[:200])
                    return self.send_raw(e.code, raw)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                try:
                    for piece in sse_codex_to_chat(iter(lambda: resp.read(4096), b""), model):
                        self.wfile.write(piece); self.wfile.flush()
                except Exception:
                    pass
                note(model, tier, 200, err=None)
                return
            code, raw = codex_chat(model, body, stream)
        elif backend == "milq":
            code, raw = milq_chat(model, body, m)
            if code in (401, 403, 404) and (has_oai or CODEX.tokens.get("access_token")):
                # hosted proxy unavailable on desktop (App Attest): fall back to your own OpenAI creds
                if has_oai:
                    code, raw = openai_chat(model, body)
                elif CODEX.tokens.get("access_token"):
                    code, raw = codex_chat("gpt-5-codex" if "5" not in model else model, body, stream)
        else:
            code, raw = openai_chat(model, body)

        ms = int((time.time() - t0) * 1000)
        tin = tout = 0
        try:
            j = json.loads(raw)
            u = j.get("usage") or {}
            tin = u.get("prompt_tokens") or u.get("input_tokens") or 0
            tout = u.get("completion_tokens") or u.get("output_tokens") or 0
        except Exception:
            pass
        if code == 200 and stream:
            pass  # streaming handled below for chat format only when passthrough
        if not stream and code == 200 and m.get("api_format") == "responses":
            conv = responses_to_chat(raw, model)
            note(model, tier, code, tin, tout, ms=ms)
            return self.send_json(200, conv)
        note(model, tier, code, tin, tout, err=None if code < 400 else raw.decode(errors="replace")[:200], ms=ms)
        return self.send_raw(code or 502, raw)

    def ui_html(self):
        return read_asset("ui.html")


def err(msg):
    return {"error": {"message": msg, "type": "invalid_request_error", "code": "milq_bridge"}}

def read_asset(name):
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", name)
    try:
        with open(base, encoding="utf-8") as f:
            return f.read()
    except Exception:
        return "<html><body>missing %s</body></html>" % name


def main():
    global HOST, PORT
    a = sys.argv[1:]; i = 0
    while i < len(a):
        if a[i] == "--host" and i+1 < len(a): HOST = a[i+1]; i += 2
        elif a[i] == "--port" and i+1 < len(a): PORT = int(a[i+1]); i += 2
        elif a[i] == "--api-key" and i+1 < len(a):
            globals()["BRIDGE_KEY"] = a[i+1]; open(KEY_FILE, "w").write(a[i+1]); i += 2
        else: i += 1
    os.makedirs(DATA_DIR, exist_ok=True)
    srv = ThreadingHTTPServer((HOST, PORT), H)
    print("MilqBridge ready")
    print("  UI:    http://%s:%d/" % (HOST, PORT))
    print("  Agent: http://%s:%d/v1" % (HOST, PORT))
    print("  Key:   %s" % BRIDGE_KEY)
    print("  Models: %s" % ", ".join(m["id"] for m in env.MODELS))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
