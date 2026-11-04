# milq-bridge
OpenAI-compatible API + real dashboard + Android app for **FreeMilk** (`freemilk.ipa`, PhoneCode 2.1.0).
Full reverse-engineering: [`docs/REVERSE.md`](docs/REVERSE.md).

## What is FreeMilk (from the decompile)
Ad-supported iOS coding workspace **and its own model provider**: you sign in (Google / ChatGPT / Apple), pick a HostedModel, and it codes. Models found in the binary:
- **Free daily** (SIPS, ad-supported): `gpt-6-astra`, `gpt-6-sol`, `gpt-6-luna`, `deepseek-ai/DeepSeek-V4.1-Flash`, `zai-org/GLM-5.3-Flash`
- **ChatGPT plan** (Codex device login, replicated 1:1 and live-tested): `gpt-5-codex`
- **Premium** (App-Attest gated on the real app; here: ad-unlock or your own OpenAI key fallback): `gpt-5.5`, `gpt-5.6-luna`, `gpt-5.6-sol`, `gpt-5.6-terra`

## Run the bridge (Python 3.11+, no dependencies)
```bash
python3 bridge/server.py --port 11434
# prints: UI / Agent base / API key  (key also at ~/.milq-bridge/bridge_key)
```
Open the dashboard → **Login tab → Start device login** → approve the one-time code at auth.openai.com with your ChatGPT account (the same flow the FreeMilk app runs — `client_id app_EMoamEEZ73f0CkXaXp7hrann`, PKCE, polling; verified live: usercode 200, token 403-pending).

## Use in any coding agent
```
OPENAI_BASE_URL=http://127.0.0.1:11434/v1
OPENAI_API_KEY=<the printed key>
```
Works with Codex CLI, opencode, aider, Continue, Cline… (everything that speaks OpenAI chat completions).
Endpoints: `/v1/models` (tier metadata), `/v1/chat/completions` (stream + non-stream, codex SSE auto-translated), `/v1/responses`, `/health`, `/usage` (per-request status, tokens, by-model, plus ChatGPT wham meter), `/status`, `/ads/unlock`, `/login/codex/*`, `/login/google/*`, `/settings`.

## Android app (APK)
`apk/` — Material 3 app (Status · Login · Models · Usage tabs). GitHub Actions builds it (no local Gradle/NDK needed); releases on `v*` tags:
[Releases](https://github.com/HakaI963/milq-bridge/releases) → `app-debug.apk`.
Point it at the bridge URL shown on your PC (same Wi‑Fi → `http://<pc-ip>:11434`, start server with `BRIDGE_LAN=1` so the APK can auto-fetch the key).

## Tests
```bash
python3 tests/test_bridge.py   # 13 offline tests: UI, tiers, gates, codecs, SSE, login contract, usage
```

## Known limits (documented in REVERSE.md §2/§5)
- FreeMilk's **mobile** account API (`/auth/*` on api.milq.ai) is gated by Apple **App Attest / Firebase App Check** hardware attestation — the bridge cannot and does not fake it; premium hosted models therefore route via your ChatGPT plan or your own OpenAI key fallback. The **web** account login (`/account/auth/google`, field `identity_token`) is implemented and used for the account side.
