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

## Runs entirely on your phone (rooted or not) — no PC needed
1. **Termux**: `pkg install -y python git && git clone https://github.com/HakaI963/milq-bridge && cd milq-bridge && BRIDGE_LAN=1 python3 bridge/server.py --port 11434`
2. Open **http://127.0.0.1:11434** in your phone browser — full dashboard (Home · Models · Free Models · Usage · Agents). Every button has a 44px+ touch target and explicit high-contrast styling.
3. **MilqBridge APK** (release): enter the server address + key once → same dashboard native in a WebView, with "Fetch my key" auto-detect.
4. Any coding agent on-device or on LAN: `OPENAI_BASE_URL=http://<phone-ip>:11434/v1`, `OPENAI_API_KEY=<bridge key>`.

### Free hosted models (GPT-6 Astra/Sol/Luna, DeepSeek V4.1 Flash, GLM 5.3 Flash)
FreeMilk serves its free catalog only to device-attested iOS clients (`X-Firebase-AppCheck`, Apple App Attest — proven hardware-bound, see docs/REVERSE.md §2). The bridge therefore becomes your client: it imports **your own** FreeMilk session + catalog once (paste, or run the bundled Termux capture proxy: `capture/milq_capture.py`), and every free model answers through your account — with SIPS/plan shown and per-request usage logged. Until an import exists the UI states this plainly rather than pretending.

![UI screenshots](docs/) — see `shots/` for the rendered dashboard on 412×915 (phone) and desktop.


## ⚡ Free models without any account — live now
The bridge serves a **keyless** free OpenAI-compatible model (Pollinations gpt-oss-20b) out of the box, and free-coding-tier models (Cerebras/DeepInfra/Groq/OpenRouter/Google — all free browser signups, no card) the moment you paste their keys in Settings:
```
curl -X POST http://127.0.0.1:11434/v1/chat/completions \
  -H "Authorization: Bearer <bridge-key>" -H "Content-Type: application/json" \
  -d '{"model":"openai-fast","messages":[{"role":"user","content":"27*13?"}]}'
→ "351"   (verified live: HTTP 200 + SSE streaming + usage counts)
```
FreeMilk itself is iOS-signup/attestation-locked (App Store: seller *Milq Incorporated*, "free…funded by ads…daily allowance" — no web/Android client exists; docs/REVERSE.md §v1.2). If you ever get one-time access to a logged-in iOS session, the import pipeline lights its exact catalog too.


## 🔑 Milq web sign-in (v1.3) — the no-iPhone door
Discovered via Milq's official `sqim` CLI (github.com/milq-ai): **api.sqim.dev runs an attestation-free Google sign-in that returns one-time codes to a localhost callback** — the bridge now *is* that CLI. Free Models tab → "Sign in with Google (Milq)" → after approval the bridge holds a real Milq token and auto-probes every service it unlocks (build machines, install links, and a catalog attempt on api.milq.ai). Results are shown verbatim — no pretending.
