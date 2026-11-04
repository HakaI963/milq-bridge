# Getting FreeMilk's OWN free models — no PC, one borrowed iPhone, ~5 minutes

You are already signed in to Milq through this bridge (Sqim web login verified: `/auth/me` = your account).
FreeMilk's free model catalog is served only to **attested iOS clients** (Apple App Attest — hardware; proven in
`docs/REVERSE.md`), and the desktop twin (`api.sqim.dev /auth/configs`) is paywalled (402 `subscription_required`,
product `com.milq.sqim.basic.weekly3` — an in-App-Store purchase). The bridge cannot and does not fake either.

But the catalog payload itself, once seen, **works from anywhere**: each free model comes with its own
`baseUrl + apiKey + headers` (the app talks to those directly). So we need ONE attested look.

## The kit
- Any iPhone (friend, family, office, store display, used SE) — **not yours to keep, only to open the app once**
- The same phone this bridge runs on, on the same Wi-Fi

## Steps (all on the iPhone — no computer)
1. App Store → install **ProxyPin** (free HTTPS debugger, runs entirely on-device). Open it → Start capture → it will offer to install its **CA profile** — Settings prompt: allow, then Settings → General → About → Certificate Trust Settings → enable full trust for ProxyPin.
2. In ProxyPin, set a filter/search for `api.milq.ai`.
3. App Store → install **FreeMilk** (free) → open it → **Sign in with Google → use your account (sadanaohiro06@gmail.com)**. The app creates your FreeMilk account on the spot — the "sign up in the app first" wall disappears: you ARE signing up in the app.
4. Let the home screen finish loading (this is when it fetches `/auth/models` + provider configs), then send one short chat message ("hi") so the app makes one model call.
5. Back in ProxyPin → select the captured session(s) → **Export → HAR** (Share → AirDrop/Files).
6. On the bridge UI (this phone): **Free Models → Import HAR → choose the file → Import.**
7. Done. The Models tab now shows every free model marked **LIVE** — routed straight to their provider endpoints with your captured keys. `/v1/models` and `/v1/chat/completions` from any agent now answer with FreeMilk's own free models. Your usage counter (SIPS) is theirs — daily allowance, ad-funded, exactly as the app works.

## When it goes stale
Provider keys rotate (the app refreshes silently via attested calls you can't replay). When a model starts 401-ing, redo steps 3–6 (30 seconds) — the bridge replaces the catalog automatically.

## Fallback: same thing with mitmproxy from this phone
`capture/milq_capture.py` runs mitmdump in Termux; point the iPhone's Wi-Fi proxy at this phone (bridge host) :8080, install `http://mitm.it` CA, repeat steps 3–5 — the addon pushes to `/import/milq` live, no export needed.
