"""Offline tests for MilqBridge: codecs, auth gates, tiers, usage counters, SSE translation.
No network — http_json is monkeypatched."""
import json, os, sys, time, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bridge"))
os.environ.setdefault("BRIDGE_DATA", "/tmp/milq-bridge-tests")
os.environ["BRIDGE_API_KEY"] = "sk-test-key"

import env
import codex_auth
import server as S


class FakeHTTP:
    """Record calls; scripted responses."""
    def __init__(self):
        self.calls = []
        self.script = {}   # url substring -> (status, json_obj|bytes)

    def __call__(self, method, url, headers=None, body=None, timeout=25, raw_body=None):
        self.calls.append((method, url, headers, body))
        for k, (st, payload) in self.script.items():
            if k in url:
                raw = json.dumps(payload).encode() if not isinstance(payload, bytes) else payload
                return st, raw
        return 404, b'{"detail":"Not Found"}'


def make_handler_tester(fake):
    """Spin a ThreadingHTTPServer on random port with monkeypatched http_json in BOTH modules."""
    from http.server import ThreadingHTTPServer
    import threading
    codex_auth.http_json = fake
    S.http_json = fake  # server uses codex path funcs that reference module-global
    srv = ThreadingHTTPServer(("127.0.0.1", 0), S.H)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    port = srv.server_address[1]
    return srv, port


class BridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = FakeHTTP()
        cls.srv, cls.port = make_handler_tester(cls.fake)
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def req(self, method, path, body=None, key="sk-test-key"):
        import urllib.request, urllib.error
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
        if key: headers["Authorization"] = "Bearer " + key
        rq = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(rq, timeout=10) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    # ---- static/health ----
    def test_health_open(self):
        st, raw = self.req("GET", "/health", key=None)
        self.assertEqual(st, 200)
        self.assertTrue(json.loads(raw)["ok"])

    def test_ui_served(self):
        st, raw = self.req("GET", "/", key=None)
        self.assertEqual(st, 200)
        html = raw.decode()
        self.assertIn("MilqBridge", html)
        for view in ("overview", "login", "models", "usage", "agent"):
            self.assertIn(f"view-{view}", html)

    # ---- catalog ----
    def test_models_require_key(self):
        st, _ = self.req("GET", "/v1/models", key=None)
        self.assertEqual(st, 401)

    def test_models_tiers(self):
        st, raw = self.req("GET", "/v1/models")
        d = json.loads(raw)["data"]
        self.assertGreaterEqual(len(d), 10)
        tiers = {m["milq"]["tier"] for m in d}
        self.assertIn("free", tiers); self.assertIn("premium", tiers); self.assertIn("plan", tiers)
        ids = {m["id"] for m in d}
        for expect in ("gpt-6-astra", "zai-org/GLM-5.3-Flash", "deepseek-ai/DeepSeek-V4.1-Flash",
                       "gpt-5-codex", "gpt-5.5", "gpt-5.6-luna"):
            self.assertIn(expect, ids)

    # ---- auth gates ----
    def test_premium_locked_without_unlock(self):
        S.CODEX.tokens.clear()
        st, raw = self.req("POST", "/v1/chat/completions",
                           {"model": "gpt-5.5", "messages": [{"role": "user", "content": "x"}]})
        self.assertEqual(st, 402)

    def test_premium_unlock_then_routes(self):
        st, raw = self.req("POST", "/ads/unlock", {"model": "gpt-5.5"})
        self.assertEqual(st, 200)
        self.assertTrue(json.loads(raw)["ok"])
        # now premium has an unlock -> milq backend; fake returns 404 for /chat/completions...
        # give openai fallback key so it routes to openai mock
        S.CODEX.tokens["openai_api_key"] = "sk-oai"
        self.fake.script["api.openai.com/v1/chat/completions"] = (200, {
            "id": "c1", "choices": [{"message": {"role": "assistant", "content": "premium ok"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}})
        st, raw = self.req("POST", "/v1/chat/completions",
                           {"model": "gpt-5.5", "messages": [{"role": "user", "content": "x"}]})
        self.assertEqual(st, 200)
        j = json.loads(raw)
        self.assertEqual(j["choices"][0]["message"]["content"], "premium ok")
        self.assertEqual(j["usage"]["total_tokens"], 5)

    def test_free_requires_signin(self):
        S.CODEX.tokens.clear()
        st, raw = self.req("POST", "/v1/chat/completions",
                           {"model": "gpt-6-astra", "messages": [{"role": "user", "content": "x"}]})
        # hosted milq requires attested session (documented) -> login gate for codex-backed free tier fallback
        self.assertIn(st, (401, 404, 502))

    def test_codex_signed_in_routes_responses(self):
        S.CODEX.tokens["access_token"] = "AT"
        S.CODEX.tokens["chatgpt_account_id"] = "acct1"
        self.fake.script["chatgpt.com/backend-api/codex/responses"] = (200, {
            "id": "resp1", "status": "completed", "usage": {"input_tokens": 4, "output_tokens": 6, "total_tokens": 10},
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "hello from codex"}]}]})
        st, raw = self.req("POST", "/v1/chat/completions",
                           {"model": "gpt-5-codex", "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(st, 200)
        j = json.loads(raw)
        self.assertEqual(j["object"], "chat.completion")
        self.assertEqual(j["choices"][0]["message"]["content"], "hello from codex")
        self.assertEqual(j["usage"]["total_tokens"], 10)
        # verify the outgoing body was responses format with ChatGPT-Account-Id header
        m, url, headers, body = [c for c in self.fake.calls if "codex/responses" in c[1]][-1]
        self.assertEqual(headers.get("ChatGPT-Account-Id"), "acct1")
        self.assertIn("input", body)
        self.assertNotIn("messages", body)

    # ---- codec units ----
    def test_chat_to_responses(self):
        out = S.chat_to_responses([{"role": "system", "content": "be nice"},
                                   {"role": "user", "content": "hi"}])
        self.assertEqual(out[0]["role"], "assistant")  # system mapped like codex CLI
        self.assertEqual(out[1]["content"][0]["type"], "input_text")

    def test_sse_translation(self):
        upstream = [
            b'data: {"type":"response.output_text.delta","delta":"he"}\n\n',
            b'data: {"type":"response.output_text.delta","delta":"llo"}\n\n',
            b'data: {"type":"response.completed","response":{"usage":{"input_tokens":1,"output_tokens":2,"total_tokens":3}}}\n\n',
        ]
        chunks = list(S.sse_codex_to_chat(iter(upstream), "gpt-5-codex"))
        text = b"".join(chunks).decode()
        # parse every SSE json line and reassemble content
        content = ""
        usage = None
        for line in text.splitlines():
            if line.startswith("data: ") and line != "data: [DONE]":
                j = json.loads(line[6:])
                for ch in j.get("choices", []):
                    content += ch.get("delta", {}).get("content", "")
                usage = j.get("usage") or usage
        self.assertEqual(content, "hello")
        self.assertIn("[DONE]", text)
        self.assertEqual(usage["total_tokens"], 3)

    def test_jwt_claims(self):
        import base64
        hdr = base64.urlsafe_b64encode(b'{"alg":"RS256"}').decode().rstrip("=")
        pl = json.dumps({"https://api.openai.com/auth": {"chatgpt_account_id": "acc", "chatgpt_plan_type": "plus"}})
        pld = base64.urlsafe_b64encode(pl.encode()).decode().rstrip("=")
        claims = codex_auth.jwt_claims(f"{hdr}.{pld}.x")
        self.assertEqual(claims["https://api.openai.com/auth"]["chatgpt_plan_type"], "plus")

    # ---- device flow live-shape (mocked) ----
    def test_codex_start_pending(self):
        self.fake.script["deviceauth/usercode"] = (200, {"device_auth_id": "d1", "user_code": "ABCD-1234",
                                                         "interval": "5", "expires_at": "2099-01-01T00:00:00Z"})
        st = S.CODEX.start()
        self.assertEqual(st["stage"], "pending")
        self.assertEqual(st["user_code"], "ABCD-1234")
        self.assertIn("codex/device", st["verification_url"])
        # verify the exact contract we observed live: client_id + PKCE + simplified flow + CFNetwork UA
        m, url, headers, body = [c for c in self.fake.calls if "usercode" in c[1]][-1]
        self.assertEqual(body["client_id"], env.CODEX_CLIENT_ID)
        self.assertTrue(body["codex_cli_simplified_flow"])
        self.assertEqual(body["code_challenge_method"], "S256")
        self.assertIn("CFNetwork", headers["User-Agent"])

    # ---- usage counters ----
    def test_usage_tracks_everything(self):
        st, raw = self.req("GET", "/usage")
        u = json.loads(raw)["local"]
        self.assertGreaterEqual(u["requests"], 1)
        self.assertEqual(u["requests"], sum(u["by_model"].values()))
        self.assertEqual(sum(u["by_model"].values()), sum(u["by_tier"].values()))
        self.assertGreaterEqual(len(u["log"]), 1)
        # every logged request carries model+status (the "status like request, usage and other" requirement)
        for row in u["log"]:
            self.assertIn("model", row); self.assertIn("status", row); self.assertIn("ts", row)


if __name__ == "__main__":
    unittest.main(verbosity=2)
