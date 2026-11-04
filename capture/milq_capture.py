#!/usr/bin/env python3
"""milq_capture.py — mitmdump addon: makes your iPhone the attestation oracle for the bridge.

Run on a laptop/PC the phone shares Wi-Fi with:

    mitmdump -s capture/milq_capture.py --listen-host 0.0.0.0 --listen-port 8080

Phone (iOS): Settings → Wi-Fi → (i) → HTTP Proxy Manual → server = PC IP, port 8080.
Install mitm CA on the phone (visit http://mitm.it, download profile, then
Settings → General → About → Certificate Trust Settings → enable full trust).
Open FreeMilk once (sign in / refresh the model list). This addon sniffs the app's
own gated API calls (App Check attested) and POSTs everything the bridge needs to
http://<PC IP>:11434/import/milq — the bridge then serves those free models to any agent.

What it harvests:
  * GET  https://api.milq.ai/auth/models   -> the hosted-model catalog (incl per-model baseUrl+apiKey+headers)
  * GET  https://api.milq.ai/auth/configs  -> feature/config payload
  * POST https://api.milq.ai/auth/*  JSON  -> access_token / refresh_token (login+refresh)
  * X-Firebase-AppCheck header from any request -> replay token (short TTL, kept fresh while the app runs)
  * provider calls (chat/completions to non-milq hosts) -> direct endpoint discovery
"""
import json, os
from urllib.parse import urlparse

BRIDGE = os.environ.get("MILQ_BRIDGE", "http://127.0.0.1:11434")

_bundle = {"catalog": None, "configs": None, "tokens": {}, "appcheck": None,
           "providers": set(), "sips": None}

def _push(force=False):
    import urllib.request
    body = dict(_bundle)
    body["providers"] = sorted(_bundle["providers"])
    data = json.dumps(body).encode()
    try:
        req = urllib.request.Request(BRIDGE + "/import/milq", data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            print("[milq_capture] pushed ->", r.read()[:200])
    except Exception as e:
        print("[milq_capture] push failed:", e)

def response(flow):
    req = flow.request
    if "api.milq.ai" not in (req.host or ""):
        # record provider hosts that look like model endpoints (chat/completions on other domains)
        if "chat/completions" in (req.path or "") or "responses" in (req.path or ""):
            hdrs = dict(req.headers)
            _bundle["providers"].add(json.dumps({
                "url": req.pretty_url.split("?")[0],
                "auth": hdrs.get("Authorization"), "headers": hdrs,
                "body": flow.request.get_text()[:400] if flow.request.get_text() else None}))
            _push()
        return
    ac = req.headers.get("X-Firebase-AppCheck") or req.headers.get("x-firebase-appcheck")
    if ac and ac != _bundle["appcheck"]:
        _bundle["appcheck"] = ac
        _push()
    path = urlparse(req.url).path
    if not flow.response:
        return
    try:
        txt = flow.response.get_text() or ""
        data = json.loads(txt) if txt.startswith("{") or txt.startswith("[") else None
    except Exception:
        data = None
    if data is None:
        return
    if path.endswith("/auth/models"):
        _bundle["catalog"] = data
        _push(); print("[milq_capture] catalog captured from", path)
    elif path.endswith("/auth/configs"):
        _bundle["configs"] = data
        _push()
    elif "auth" in path and isinstance(data, dict):
        for k in ("access_token", "refresh_token", "expires_in", "sip_credits", "sips", "dailySips"):
            if k in data:
                _bundle["tokens"][k] = data[k]
        if _bundle["tokens"]:
            _push()

def error(flow):
    print("[milq_capture] flow error:", flow.error.msg if flow.error else "?")
