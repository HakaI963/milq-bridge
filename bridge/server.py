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
from codex_auth import CodexLogin, GoogleLogin, SqimLogin, http_json, jwt_claims

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
SQIM  = SqimLogin(CODEX.tokens, f"http://{HOST}:{PORT}")
SQIM_PROBE = {"ran": False, "result": None}

# ---------------- FreeMilk (milq) import: catalog + appcheck + session ----------------
MILQ = {"catalog": None, "configs": None, "tokens": {}, "appcheck": None,
        "providers": [], "imported_at": None}
MILQ_FILE = os.path.join(DATA_DIR, "milq_import.json")
try:
    if os.path.exists(MILQ_FILE):
        MILQ.update(json.load(open(MILQ_FILE)))
except Exception:
    pass

def save_milq():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = MILQ_FILE + ".tmp"
        json.dump(MILQ, open(tmp, "w"), indent=1)
        os.chmod(tmp, 0o600)
        os.replace(tmp, MILQ_FILE)
    except Exception:
        pass

def parse_catalog(cat):
    """Flatten FreeMilk's HostedModelListing (any nesting) into model entries.
    Every entry keeps: id, name, tier, api_format, base_url, api_key, headers."""
    out = {}
    def walk(node, tier_hint=None):
        if isinstance(node, list):
            for x in node: walk(x, tier_hint)
            return
        if not isinstance(node, dict): return
        mid = node.get("id") or node.get("model") or node.get("modelId") or node.get("model_id")
        if isinstance(mid, str) and ("apiFormat" in node or "api_format" in node or "baseUrl" in node
                                     or "base_url" in node or "apiKey" in node or "tier" in node or node.get("name")):
            fmt = node.get("apiFormat") or node.get("api_format") or "chat_completions"
            base = node.get("baseUrl") or node.get("base_url") or node.get("url") or node.get("endpoint")
            key = node.get("apiKey") or node.get("api_key") or node.get("key") or node.get("token")
            hdr = node.get("headers") or node.get("modelHeaders") or node.get("model_headers") or {}
            tier = node.get("tier") or tier_hint or "free"
            out[mid] = dict(id=mid, name=node.get("name") or node.get("displayName") or node.get("display") or mid,
                            tier=tier, api_format=fmt, base_url=base, api_key=key,
                            headers=hdr if isinstance(hdr, dict) else {},
                            context_window=node.get("contextWindow") or node.get("context_window"),
                            accepts_images=node.get("acceptsImages"), raw=node)
            return
        # container keys: try to propagate tier hints
        for k, v in node.items():
            th = tier_hint
            if k in ("freeModels", "free_models", "free"): th = "free"
            elif k in ("premiumModels", "premium_models", "premium"): th = "premium"
            if isinstance(v, (dict, list)):
                walk(v, th)
    walk(cat)
    return out

CATALOG = parse_catalog(MILQ.get("catalog") or {}) if MILQ.get("catalog") else {}

def refresh_catalog():
    global CATALOG
    CATALOG = parse_catalog(MILQ.get("catalog") or {}) if MILQ.get("catalog") else {}
    # merge known static catalog: imported entries win
    for m in env.MODELS:
        if m["id"] not in CATALOG:
            CATALOG[m["id"]] = dict(id=m["id"], name=m["display"], tier=m["tier"],
                                    api_format=m["api_format"], base_url=None, api_key=None,
                                    headers={}, context_window=m["context_window"], raw=m)

def milq_authed_headers():
    h = {"Content-Type": "application/json", "Accept": "application/json",
         "User-Agent": env.cfnetwork_ua()}
    if MILQ.get("appcheck"):
        h["X-Firebase-AppCheck"] = MILQ["appcheck"]
    tok = (MILQ.get("tokens") or {}).get("access_token")
    if tok:
        h["Authorization"] = "Bearer " + tok
    if MILQ.get("session_cookie"):
        h["Cookie"] = "session=" + MILQ["session_cookie"]
    return h

def fetch_milq_models():
    """Replay the app's own catalog fetch through the bridge (needs imported appcheck+session; the web
    account cookie is also sent — if FreeMilk serves the catalog to web sessions, free models light up)."""
    code, raw = http_json("GET", env.MILQ_BASE + "/auth/models", headers=milq_authed_headers(), timeout=20)
    if code == 200:
        try:
            MILQ["catalog"] = json.loads(raw)
            MILQ["imported_at"] = int(time.time())
            refresh_catalog(); save_milq()
            return 200, {"ok": True, "models": len(CATALOG)}
        except Exception as e:
            return 502, {"error": str(e)}
    return code, {"detail": raw[:500].decode(errors="replace")}

def refresh_milq_tokens():
    """Rotate the milq session like the app does (needs appcheck)."""
    t = MILQ.get("tokens") or {}
    if not t.get("refresh_token"):
        return 400, {"error": "no refresh_token imported"}
    code, raw = http_json("POST", env.MILQ_BASE + "/auth/refresh",
                          headers=milq_authed_headers(), body={"refresh_token": t["refresh_token"]}, timeout=20)
    if code == 200:
        try:
            j = json.loads(raw)
            for k in ("access_token", "refresh_token", "expires_in"):
                if k in j: t[k] = j[k]
            save_milq(); return 200, {"ok": True}
        except Exception as e:
            return 502, {"error": str(e)}
    return code, {"detail": raw[:400].decode(errors="replace")}

def alt_key(name):
    """Alt provider keys: stored via /settings (env fallback)."""
    envname = env.ALT_PROVIDERS.get(name, {}).get("key_env")
    return (CODEX.tokens.get("alt_keys") or {}).get(name) or (os.environ.get(envname) if envname else None)

def alt_chat(mid, body, stream):
    """Route alt:<provider> or bare pollinations ids."""
    prov = None
    real = mid
    if ":" in mid and mid.split(":")[0] in env.ALT_PROVIDERS:
        prov, real = mid.split(":", 1)
    elif mid in ("openai-fast", "openai", "gpt-oss", "gpt-oss-20b"):
        prov, real = "pollinations", "openai-fast"
    if not prov:
        return None
    cfg = env.ALT_PROVIDERS[prov]
    key = alt_key(prov)
    if cfg["key_env"] and not key:
        return 402, json.dumps({"error": {"message": f"{prov} needs a free API key — add it in Settings (Agents tab)", "type": "key_required"}}).encode()
    path = cfg.get("path", "/chat/completions")
    url = cfg["base"].rstrip("/") + path
    hdrs = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0 (compatible; milq-bridge/1.2)",
            "Accept": "text/event-stream" if stream else "application/json"}
    if cfg["auth"] == "bearer" and key:
        hdrs["Authorization"] = "Bearer " + key
    if prov == "pollinations":
        hdrs["X-Referrer"] = "milq-bridge"
    out = dict(body); out["model"] = real
    if stream: out["stream"] = True
    return http_json("POST", url, headers=hdrs, body=out, timeout=180)

def provider_chat(mid, body, stream):
    """Call the captured per-model provider endpoint exactly like the app does."""
    m = CATALOG.get(mid) or {}
    base = m.get("base_url")
    if not base:
        # provider hosts harvested from live calls
        for p in MILQ.get("providers") or []:
            try:
                pj = json.loads(p) if isinstance(p, str) else p
                if pj.get("url", "").find(mid) >= 0 or mid in (pj.get("body") or ""):
                    base = pj.get("url"); m = {"api_key": None, "headers": pj.get("headers") or {}}
            except Exception: continue
    if not base:
        return None
    url = base.rstrip("/")
    if not url.endswith("chat/completions") and "responses" not in url:
        url += "/chat/completions"
    hdrs = {"Content-Type": "application/json", "User-Agent": env.cfnetwork_ua(),
            "Accept": "text/event-stream" if stream else "application/json"}
    key = m.get("api_key")
    if key and not str(key).startswith("x-"):
        hdrs["Authorization"] = "Bearer " + str(key)
    for k, v in (m.get("headers") or {}).items():
        hdrs[k] = str(v)
    if isinstance(hdrs.get("Authorization"), str) and hdrs["Authorization"] == "Bearer None":
        hdrs.pop("Authorization")
    if m.get("api_format") == "anthropic_messages":
        if key: hdrs["x-api-key"] = str(key); hdrs.pop("Authorization", None)
        hdrs["anthropic-version"] = "2023-06-01"
        url = base.rstrip("/").replace("/chat/completions", "") + "/messages"
        body = dict(body); body.pop("stream", None)
        if stream: body["stream"] = True
    out = dict(body)
    out["model"] = mid
    if stream: out["stream"] = True
    return http_json("POST", url, headers=hdrs, body=out, timeout=240)


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


def import_har(har):
    """Parse a HAR export captured on any iPhone running the real FreeMilk app
    (ProxyPin / mitm). Extracts: App Check header, milq bearer tokens, the
    /auth/models catalog, provider baseUrl+keys from live chat calls."""
    out = {"appcheck": False, "bearer": False, "catalog_entries": 0, "provider_calls": 0, "notes": []}
    entries = ((har or {}).get("log") or {}).get("entries") or []
    best_catalog, best_len = None, 0
    for e in entries:
        req = e.get("request") or {}; res = e.get("response") or {}
        url = req.get("url") or ""
        hdrs = {h.get("name", "").lower(): h.get("value", "") for h in req.get("headers") or []}
        if hdrs.get("x-firebase-appcheck", "").count(".") == 2:
            MILQ["appcheck"] = hdrs["x-firebase-appcheck"]; out["appcheck"] = True
        if "api.milq.ai" in url:
            auth = hdrs.get("authorization", "")
            if auth.lower().startswith("bearer ") and auth[7:].strip():
                MILQ.setdefault("tokens", {})["access_token"] = auth[7:].strip()
                out["bearer"] = True
            path = url.split("?")[0]
            if path.endswith(("/auth/models", "/auth/configs")) and (res.get("status") or 0) == 200:
                txt = (res.get("content") or {}).get("text") or ""
                try:
                    cand = json.loads(txt)
                except Exception:
                    cand = None
                if cand and len(txt) > best_len:
                    best_catalog, best_len = cand, len(txt)
                    out["catalog_entries"] += 1
        else:
            path = url.split("?")[0]
            if path.endswith("/chat/completions") or path.endswith("/responses") or path.endswith("/messages"):
                base = path[: -(len("/chat/completions") if path.endswith("/chat/completions") else (len("/responses") if path.endswith("/responses") else len("/messages")))].rstrip("/")
                kv = {}
                a = hdrs.get("authorization", "")
                if a.lower().startswith("bearer "): kv["apiKey"] = a[7:]
                if hdrs.get("x-api-key"): kv["apiKey"] = hdrs["x-api-key"]
                extra = {k: v for k, v in hdrs.items() if k.startswith("x-") and k not in ("x-api-key",)}
                mdl = None
                try:
                    post = (req.get("postData") or {}).get("text") or ""
                    mdl = json.loads(post).get("model") if post else None
                except Exception:
                    pass
                if base and (kv.get("apiKey") or extra):
                    MILQ.setdefault("providers_hints", []).append({"base": base, "model": mdl, **kv, "headers": extra})
                    out["provider_calls"] += 1
                    if best_catalog is None: best_catalog = {"models": []}
                    if isinstance(best_catalog, dict):
                        best_catalog.setdefault("freeModels", {"models": []})["models"].append(
                            {"id": mdl or base, "name": (mdl or base), "tier": "free", "apiFormat": "chat_completions",
                             "baseUrl": base, "apiKey": kv.get("apiKey"), "headers": extra})
    if best_catalog:
        MILQ["catalog"] = best_catalog
        MILQ["imported_at"] = int(time.time())
        refresh_catalog(); save_milq()
        out["catalog_models"] = len(CATALOG)
        out["notes"].append("catalog imported -> free models route through captured provider keys")
    elif entries:
        out["notes"].append("no /auth/models response found in HAR; open FreeMilk once WITH the proxy on (model list fetch happens at app start), re-export, re-import")
    else:
        out["notes"].append("empty/no HAR entries")
    save_milq()
    return out

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
            data = []
            seen = set()
            for mid, cm in CATALOG.items():
                seen.add(mid)
                mm = {"id": mid, "object": "model", "created": 1760000000,
                      "owned_by": "freemilk",
                      "milq": {"tier": cm.get("tier") or "free", "display": cm.get("name") or mid,
                               "api_format": cm.get("api_format") or "chat_completions",
                               "context_window": cm.get("context_window") or 128000,
                               "accepts_images": cm.get("accepts_images"),
                               "is_default": False,
                               "backend": "milq-captured" if (cm.get("base_url") or cm.get("api_key")) else (env.model_by_id(mid) or {}).get("backend", "milq"),
                               "mode": "agent"}}
                if not tier or mm["milq"]["tier"] == tier: data.append(mm)
            for am in env.alt_models({(env.ALT_PROVIDERS[p].get("key_env") or "POLL"): True
                                      for p in env.ALT_PROVIDERS if p == "pollinations" or alt_key(p)}):
                if am["id"] in seen: continue
                if not tier or am["milq"]["tier"] == tier: data.append(am)
            return self.send_json(200, {"object": "list", "data": data})
        if p.startswith("/v1/models/"):
            if not authed(self.headers): return self.send_json(401, err("invalid key"))
            mid = p[len("/v1/models/"):]
            cm = CATALOG.get(mid)
            if cm:
                return self.send_json(200, {"id": mid, "object": "model", "owned_by": "freemilk",
                                            "milq": {k: cm.get(k) for k in ("tier","name","api_format","context_window")}})
            m = env.model_by_id(mid)
            return self.send_json(200 if m else 404, env.oai_model(m) if m else err("not found"))
        if p == "/mock/chat/completions":
            # offline demo provider so the free-model path can be exercised without milq attestation
            body = self.body()
            model = body.get("model", "mock")
            last = ""
            for mm in reversed(body.get("messages", [])):
                if mm.get("role") == "user":
                    last = str(mm.get("content", ""))[:120]
                    break
            reply = ("[mock milq-proxy " + model + "] received: " + last +
                     " — this is how captured FreeMilk free-model answers will flow.")
            return self.send_json(200, {"id": "chatcmpl-mock", "object": "chat.completion",
                                        "created": int(time.time()), "model": model,
                                        "choices": [{"index": 0, "message": {"role": "assistant", "content": reply},
                                                     "finish_reason": "stop"}],
                                        "usage": {"prompt_tokens": 5, "completion_tokens": 20, "total_tokens": 25}})
        if p in ("/mock", "/mock/"):
            q = urllib.parse.parse_qs(self.path.split("?")[1] if "?" in self.path else "")
            return self.send_json(200, {"mock": "ok", "model": (q.get("model") or ["?"])[0]})
        if p == "/sqim/callback":
            q = urllib.parse.parse_qs(self.path.split("?")[1] if "?" in self.path else "")
            code = (q.get("code") or [""])[0]; state = (q.get("state") or [""])[0]
            if not code:
                errq = (q.get("error") or ["cancelled"])[0]
                return self.send_raw(200, f"<html><body style='background:#020617;color:#F8FAFC;font-family:monospace;padding:40px'><h2>Sign-in {errq}</h2><p>Close this tab.</p></body></html>".encode(), "text/html; charset=utf-8")
            ok, info = SQIM.callback(code, state)
            if ok:
                CODEX._save()
                global SQIM_PROBE
                SQIM_PROBE = {"ran": True, "result": SQIM.probe()}
                save_milq()
                return self.send_raw(200, ("<html><body style='background:#020617;color:#F8FAFC;font-family:monospace;padding:40px'>"
                    "<h2 style='color:#22C55E'>✓ Signed in to Milq (Sqim web login)</h2>"
                    f"<pre>{json.dumps(SQIM_PROBE['result'], indent=1)[:3000]}</pre>"
                    "<p>Return to the bridge — the Agents tab now carries this session. Close this tab.</p></body></html>").encode(), "text/html; charset=utf-8")
            return self.send_raw(200, f"<html><body style='background:#020617;color:#EF4444;font-family:monospace;padding:40px'><h2>Sign-in failed</h2><pre>{str(info)[:800]}</pre></body></html>".encode(), "text/html; charset=utf-8")
        if p == "/login/sqim/status":
            d = SQIM.status(); d["probe"] = SQIM_PROBE
            return self.send_json(200, d)
        if p == "/milq/status":
            if not authed(self.headers): return self.send_json(401, err("invalid key"))
            summ = MILQ.get("summary") or {}
            return self.send_json(200, {"imported": bool(MILQ.get("catalog") or MILQ.get("tokens") or MILQ.get("session_cookie")),
                                        "catalog_models": len(CATALOG),
                                        "has_appcheck": bool(MILQ.get("appcheck")),
                                        "has_session": bool((MILQ.get("tokens") or {}).get("access_token") or MILQ.get("session_cookie")),
                                        "sips": MILQ.get("sips"),
                                        "account_summary": {"status": summ.get("status"), "ok": (summ.get("data") or {}).get("user") is not None} if summ else None,
                                        "imported_at": MILQ.get("imported_at"),
                                        "providers": len(MILQ.get("providers") or []),
                                        "sqim": bool(SQIM.tokens.get("access_token"))})
        if p == "/milq/refresh":
            if not authed(self.headers): return self.send_json(401, err("invalid key"))
            st, det = fetch_milq_models()
            return self.send_json(200 if st == 200 else st or 502, det)
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
        if p == "/mock/chat/completions":
            body = self.body()
            model = body.get("model", "mock")
            last = ""
            for mm in reversed(body.get("messages", [])):
                if mm.get("role") == "user":
                    last = str(mm.get("content", ""))[:120]; break
            reply = "[mock milq-proxy " + model + "] received: " + last + " — captured FreeMilk free-model traffic will answer like this."
            return self.send_json(200, {"id": "chatcmpl-mock", "object": "chat.completion",
                "created": int(time.time()), "model": model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 20, "total_tokens": 25}})
        body = self.body()
        if p == "/import/milq":
            # open on loopback / LAN flag (capture addon posts here); still requires BRIDGE_LAN or loopback
            ip = self.client_address[0]
            if not (ip.startswith("127.") or ip.startswith("::1") or os.environ.get("BRIDGE_LAN") == "1"):
                return self.send_json(403, err("import only from loopback or BRIDGE_LAN=1"))
            for k in ("catalog", "configs", "appcheck"):
                if body.get(k):
                    MILQ[k] = body[k]
            if body.get("session_cookie"):
                MILQ["session_cookie"] = body["session_cookie"]
                st, raw = http_json("GET", env.MILQ_BASE + "/account/api/summary",
                                    headers={"Cookie": "session=" + body["session_cookie"],
                                             "User-Agent": env.cfnetwork_ua(), "Accept": "application/json"}, timeout=20)
                try:
                    j = json.loads(raw) if st == 200 else None
                except Exception:
                    j = None
                MILQ["summary"] = {"status": st, "data": j}
                if isinstance(j, dict):
                    sc = j.get("sip_credits") or {}
                    MILQ["sips"] = sc.get("sips")
                    if isinstance(j.get("tokens"), dict):
                        MILQ["tokens"].update(j["tokens"])
            if isinstance(body.get("tokens"), dict):
                MILQ.setdefault("tokens", {}).update(body["tokens"])
            if isinstance(body.get("providers"), list):
                MILQ["providers"] = (MILQ.get("providers") or [])[:20] + [p for p in body["providers"] if p not in (MILQ.get("providers") or [])]
            MILQ["imported_at"] = int(time.time())
            refresh_catalog(); save_milq()
            return self.send_json(200, {"ok": True, "catalog_models": len(CATALOG),
                                        "appcheck": bool(MILQ.get("appcheck")),
                                        "session": bool((MILQ.get("tokens") or {}).get("access_token"))})
        if p == "/milq/refresh_tokens":
            if not authed(self.headers): return self.send_json(401, err("invalid key"))
            st, det = refresh_milq_tokens()
            return self.send_json(200 if st == 200 else st or 502, det)
        if p == "/login/sqim/start":
            return self.send_json(200, SQIM.start())
        if p == "/login/sqim/configs":
            if not authed(self.headers): return self.send_json(401, err("invalid key"))
            ch = body.get("channel") or "stable"
            st, j = SQIM.fetch_configs(ch)
            if st == 200 and isinstance(j, dict) and (j.get("models") or j.get("freeModels") or j.get("premiumModels")):
                MILQ["catalog"] = j
                MILQ["imported_at"] = int(time.time())
                refresh_catalog(); save_milq()
                return self.send_json(200, {"ok": True, "source": "sqim-provider-configs", "channel": ch,
                                            "catalog_models": len(CATALOG), "tiered": {"free": sum(1 for v in CATALOG.values() if v.get("tier")=="free"), "premium": sum(1 for v in CATALOG.values() if v.get("tier")=="premium")}})
            return self.send_json(200 if st == 200 else st or 502, {"ok": False, "channel": ch, "response": j})
        if p == "/import/har":
            ip = self.client_address[0]
            if not (ip.startswith("127.") or ip.startswith("::1") or os.environ.get("BRIDGE_LAN") == "1"):
                return self.send_json(403, err("import only from loopback or BRIDGE_LAN=1"))
            rep = import_har(body)
            return self.send_json(200, rep)
        if p == "/login/sqim/probe":
            if not authed(self.headers): return self.send_json(401, err("invalid key"))
            global SQIM_PROBE
            SQIM_PROBE = {"ran": True, "result": SQIM.probe()}
            return self.send_json(200, SQIM_PROBE)
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
            if isinstance(body.get("alt_keys"), dict):
                CODEX.tokens.setdefault("alt_keys", {}).update(body["alt_keys"])
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
        m = env.model_by_id(model) or {"tier": "free", "backend": "openai", "id": model}
        # captured catalog wins for routing decisions
        cm = CATALOG.get(model) or {}
        if cm.get("base_url") or cm.get("api_key"):
            m = dict(m); m.update({"tier": cm.get("tier") or m["tier"], "backend": "milq",
                                   "api_format": cm.get("api_format") or m.get("api_format", "chat_completions")})
        elif cm.get("tier"):
            m = dict(m); m["tier"] = cm["tier"]
        tier, backend = m["tier"], m.get("backend", "openai")

        # gate premium
        unlocks = CODEX.tokens.get("ad_unlocks", {})
        plan = CODEX.tokens.get("chatgpt_plan_type") or ""
        has_oai = bool(CODEX.tokens.get("openai_api_key") or os.environ.get("OPENAI_API_KEY"))
        has_provider = CATALOG.get(model, {}).get("base_url") or CATALOG.get(model, {}).get("api_key")
        if tier == "premium" and unlocks.get(model, 0) < time.time() and not (has_oai or plan or has_provider):
            note(model, tier, 402, err="premium locked", ms=int((time.time()-t0)*1000))
            return self.send_json(402, {"error": {"message": f"'{model}' is premium: subscribe or POST /ads/unlock", "type": "premium_required"}})
        has_provider = (CATALOG.get(model) or {}).get("base_url") or (CATALOG.get(model) or {}).get("api_key") or has_provider
        if tier in ("free", "plan") and not CODEX.tokens.get("access_token") and backend == "codex" and not has_provider and model.split(":")[0] not in env.ALT_PROVIDERS and model not in ("openai-fast","openai","gpt-oss","gpt-oss-20b"):
            note(model, tier, 401, err="not signed in", ms=int((time.time()-t0)*1000))
            return self.send_json(401, {"error": {"message": "not signed in — open / and connect ChatGPT (device login)", "type": "login_required"}})

        if model.split(":")[0] in env.ALT_PROVIDERS or model in ("openai-fast", "openai", "gpt-oss", "gpt-oss-20b"):
            got = alt_chat(model, body, stream)
            if got:
                code, raw = got
                ms = int((time.time()-t0)*1000)
                tin=tout=0
                try:
                    j=json.loads(raw); u=j.get("usage") or {}
                    tin=u.get("prompt_tokens") or 0; tout=u.get("completion_tokens") or 0
                except Exception: pass
                note(model, "free", code, tin, tout, err=None if code<400 else raw.decode(errors="replace")[:200], ms=ms)
                return self.send_raw(code or 502, raw, "text/event-stream" if stream else "application/json")
        if tier in ("free", "premium") and backend == "milq":
            got = provider_chat(model, body, stream)
            if got:
                code, raw = got
                ms = int((time.time()-t0)*1000)
                tin=tout=0
                try:
                    j=json.loads(raw); u=j.get("usage") or {}
                    tin=u.get("prompt_tokens") or u.get("input_tokens") or 0
                    tout=u.get("completion_tokens") or u.get("output_tokens") or 0
                except Exception: pass
                note(model, tier, code, tin, tout, err=None if code<400 else raw.decode(errors="replace")[:200], ms=ms)
                return self.send_raw(code or 502, raw)
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


refresh_catalog()

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
