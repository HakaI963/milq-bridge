"""Codex/ChatGPT device login + token store — the replicated FreeMilk login environment.

Flow (identical to CoderKit/ChatGPTAuth.swift, LIVE-VERIFIED against auth.openai.com):
  1. POST /api/accounts/deviceauth/usercode  (CFNetwork UA required!)  -> device_auth_id, user_code
  2. user opens https://auth.openai.com/codex/device?...&user_code confirmation, approves
  3. POST /api/accounts/deviceauth/token {client_id, device_auth_id, user_code, verifier}
     -> 403 authorization_pending while waiting, 200 with {authorization_code, id_token} when done
  4. POST /oauth/token {grant_type: authorization_code, code, code_verifier, redirect_uri, client_id}
     -> {access_token, refresh_token, id_token}
  5. GET https://api.openai.com/auth (with bearer) -> chatgpt_account_id, plan type
"""
import base64, json, os, secrets, threading, time, urllib.error, urllib.parse, urllib.request

import env


def http_json(method, url, headers=None, body=None, timeout=25, raw_body=None):
    data = raw_body if raw_body is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return 0, str(e).encode()


def jwt_claims(tok):
    try:
        p = tok.split(".")[1]
        p += "=" * (-len(p) % 4)
        return json.loads(base64.urlsafe_b64decode(p))
    except Exception:
        return {}


class CodexLogin:
    """One active device-flow session + persisted tokens."""

    def __init__(self, store_path):
        self.store_path = store_path
        self.lock = threading.Lock()
        self.state = {"stage": "idle", "user_code": None, "verification_url": None,
                      "expires_at": None, "error": None, "polls": 0, "approved_at": None}
        self.tokens = self._load()

    # ---------- persistence ----------
    def _load(self):
        try:
            with open(self.store_path) as f:
                return json.load(f)
        except Exception:
            return {}

    def _save(self):
        os.makedirs(os.path.dirname(self.store_path) or ".", exist_ok=True)
        tmp = self.store_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.tokens, f, indent=1)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.store_path)

    # ---------- flow ----------
    def start(self):
        verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
        challenge = base64.urlsafe_b64encode(hashlib_sha(verifier)).decode().rstrip("=")
        state = secrets.token_urlsafe(16)
        body = {"client_id": env.CODEX_CLIENT_ID, "scope": env.SCOPE,
                "redirect_uri": "http://localhost:1455/deviceauth/callback",
                "state": state, "code_challenge": challenge,
                "code_challenge_method": "S256",
                "id_token_add_organizations": True,
                "codex_cli_simplified_flow": True}
        code, raw = http_json("POST", env.DEVICE_USERCODE,
                              headers={"Content-Type": "application/json",
                                       "User-Agent": env.cfnetwork_ua(),
                                       "Accept": "application/json"}, body=body)
        if code != 200:
            with self.lock:
                self.state.update(stage="error", error=raw.decode(errors="replace")[:300])
            return self.status()
        j = json.loads(raw)
        url = (env.DEVICE_VERIFY + "?response_type=code&client_id=" + env.CODEX_CLIENT_ID +
               "&scope=" + urllib.parse.quote(env.SCOPE) +
               "&redirect_uri=" + urllib.parse.quote(body["redirect_uri"]) +
               "&state=" + state + "&code_challenge=" + challenge +
               "&code_challenge_method=S256&id_token_add_organizations=true&codex_cli_simplified_flow=true")
        with self.lock:
            self._session = {"verifier": verifier, "state": state, "body": j, "req": body}
            self.state.update(stage="pending", user_code=j["user_code"],
                              verification_url=url, expires_at=j.get("expires_at"),
                              error=None, polls=0, approved_at=None)
        threading.Thread(target=self._poll_loop, daemon=True).start()
        return self.status()

    def _poll_loop(self):
        sess = getattr(self, "_session", None)
        if not sess: return
        deadline = time.time() + 14 * 60
        while time.time() < deadline:
            time.sleep(int(sess["body"].get("interval") or 5))
            poll = {"client_id": env.CODEX_CLIENT_ID,
                    "device_auth_id": sess["body"]["device_auth_id"],
                    "user_code": sess["body"]["user_code"],
                    "verifier": sess["verifier"]}
            code, raw = http_json("POST", env.DEVICE_TOKEN,
                                  headers={"Content-Type": "application/json",
                                           "User-Agent": env.cfnetwork_ua()}, body=poll, timeout=20)
            with self.lock:
                self.state["polls"] += 1
            if code == 200:
                j = json.loads(raw)
                acode = j.get("authorization_code")
                if acode:
                    ok = self._exchange(acode, sess["verifier"], sess["req"]["redirect_uri"])
                    with self.lock:
                        self.state.update(stage="ready" if ok else "error",
                                          approved_at=time.time(),
                                          error=None if ok else "token exchange failed")
                    return
            elif code == 403 or b"pending" in raw:
                continue
            else:
                with self.lock:
                    self.state.update(stage="error", error=raw.decode(errors="replace")[:300])
                return
        with self.lock:
            self.state.update(stage="expired", error="code expired — start again")

    def _exchange(self, acode, verifier, redirect_uri):
        form = urllib.parse.urlencode({
            "grant_type": "authorization_code", "code": acode,
            "client_id": env.CODEX_CLIENT_ID, "redirect_uri": redirect_uri,
            "code_verifier": verifier}).encode()
        code, raw = http_json("POST", env.OAUTH_TOKEN,
                              headers={"Content-Type": "application/x-www-form-urlencoded",
                                       "User-Agent": env.cfnetwork_ua()}, raw_body=form)
        if code != 200:
            return False
        j = json.loads(raw)
        self.tokens.update({
            "access_token": j.get("access_token"),
            "refresh_token": j.get("refresh_token"),
            "id_token": j.get("id_token"),
            "obtained_at": int(time.time()),
            "expires_in": j.get("expires_in"),
        })
        claims = jwt_claims(j.get("id_token") or "")
        auth = claims.get("https://api.openai.com/auth", {})
        self.tokens["chatgpt_account_id"] = auth.get("chatgpt_account_id")
        self.tokens["chatgpt_plan_type"] = auth.get("chatgpt_plan_type")
        self._save()
        return True

    def refresh(self):
        if not self.tokens.get("refresh_token"):
            return False
        form = urllib.parse.urlencode({
            "grant_type": "refresh_token", "refresh_token": self.tokens["refresh_token"],
            "client_id": env.CODEX_CLIENT_ID}).encode()
        code, raw = http_json("POST", env.OAUTH_TOKEN,
                              headers={"Content-Type": "application/x-www-form-urlencoded",
                                       "User-Agent": env.cfnetwork_ua()}, raw_body=form)
        if code != 200:
            return False
        j = json.loads(raw)
        self.tokens["access_token"] = j.get("access_token") or self.tokens.get("access_token")
        if j.get("refresh_token"):
            self.tokens["refresh_token"] = j["refresh_token"]
        self._save()
        return True

    def status(self):
        with self.lock:
            s = dict(self.state)
        s["signed_in"] = bool(self.tokens.get("access_token"))
        s["plan"] = self.tokens.get("chatgpt_plan_type")
        s["account_id"] = self.tokens.get("chatgpt_account_id")
        s["access_token_tail"] = (self.tokens.get("access_token") or "")[-6:]
        s.pop("_session", None)
        return s


def hashlib_sha(s):
    import hashlib
    return hashlib.sha256(s.encode()).digest()


# ---------- Google (FreeMilk account web side; mobile side is device-gated) ----------
class GoogleLogin:
    def __init__(self, store, port):
        self.tokens = store
        self.port = port

    def authorize_url(self):
        return (env.GOOGLE_AUTH_URL + "?client_id=" + env.GOOGLE_IOS_CLIENT +
                "&redirect_uri=" + urllib.parse.quote(env.GOOGLE_IOS_REVERSED + ":/oauth2callback") +
                "&response_type=code&scope=" + urllib.parse.quote("openid email profile") +
                "&code_challenge_method=S256&prompt=select_account")

    def exchange(self, code):
        form = urllib.parse.urlencode({
            "code": code, "client_id": env.GOOGLE_IOS_CLIENT,
            "redirect_uri": env.GOOGLE_IOS_REVERSED + ":/oauth2callback",
            "grant_type": "authorization_code"}).encode()
        st, raw = http_json("POST", env.GOOGLE_TOKEN_URL,
                            headers={"Content-Type": "application/x-www-form-urlencoded"}, raw_body=form)
        if st != 200:
            return False, raw.decode(errors="replace")[:300]
        j = json.loads(raw)
        idt = j.get("id_token", "")
        claims = jwt_claims(idt)
        self.tokens["google"] = {"id_token": idt, "access_token": j.get("access_token"),
                                 "email": claims.get("email")}
        # FreeMilk account: verified contract
        st2, raw2 = http_json("POST", env.MILQ_WEB_LOGIN,
                              headers={"Content-Type": "application/json",
                                       "User-Agent": env.cfnetwork_ua()},
                              body={"identity_token": idt})
        self.tokens["milq_account"] = {"status": st2, "body": raw2.decode(errors="replace")[:400]}
        return st2 == 200, self.tokens["milq_account"]["body"]
