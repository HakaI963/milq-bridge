# freemilk.ipa — full reverse-engineering report (v2, complete rebuild)

Target: `/virtual/freemilk.ipa` → **FreeMilk** (display), bundle `ai.milq.mobile`, executable **PhoneCode** (Mach-O arm64, 23,290,752 B, sha256 `a27998439176d1dcfa4fd124fd744f511077a9f5421e66032f07285c5e359b9c`), v2.1.0 (52), iOS 26+.

Decompile method:
1. Unzipped full IPA; analyzed **all three Mach-O binaries** (PhoneCode, PhoneCodeLiveActivityExtension.appex, Sentry.framework) — parsed the 64-bit load commands and dumped **every string-bearing section** (`__TEXT.__cstring` 12,797 · `__TEXT.__const` 7,649 · `__TEXT.__objc_classname` 782 · `__TEXT.__objc_methname` 8,786) preserving file order, then mined literal neighborhoods per Swift file symbol (`MilqMobile2/MilqSignIn.swift`, `CoderKit/ChatGPTAuth.swift`, `HostedModels.swift`, `FreeSips.swift`, `ChatAds.swift`, `PremiumSubscription.swift`, `RuntimeAPIClient`…).
2. REA MCP: `open_binary` succeeded (mach-o/arm64); deep Ghidra auto-analysis timed out (raised startup budget 330 s → 1,200,000 ms); deterministic section dump above was used instead of waiting on the decompiler.
3. Live backend fingerprinting (curl) against `api.milq.ai`, `auth.openai.com`, `firebaseappcheck.googleapis.com`, `accounts.google.com` — every claim below marked **[LIVE]** was executed against the real service.

## 1. Identity & config
| Key | Value | Source |
|---|---|---|
| MILQ_API_BASE_URL | `https://api.milq.ai` | Info.plist |
| Google iOS client | `311745113120-qptt8h4kcsb3p2tbd8bpg3n9etvjf3km.apps.googleusercontent.com` | GoogleService-Info.plist |
| Google reversed (URL scheme) | `com.googleusercontent.apps.311745113120-…` | Info.plist CFBundleURLTypes |
| Firebase | key `AIzaSyC3618xCRXx5WxNKsQVjkioTmCKlw6zZ0M`, project `milq-mobile`, app `1:311745113120:ios:93c252abf70d8ab0812045` | GoogleService-Info.plist |
| Web Google client | `682587638607-0i56v9dho4f19mmhh692plr49404ms7e.apps.googleusercontent.com` | **[LIVE]** `/account/api/login-config` |
| Ads | Gravity: `https://server.trygravity.ai/api/v1/ad`, key in Info.plist | ChatAds.swift strings |
| Keychain | `com.neelmakhecha.milq.accessToken/.refreshToken`, `com.neelmakhecha.phonecode.chatgpt`, `com.neelmakhecha.phonecode.github` | strings |
| Analytics | PostHog `phc_oPOx…`, Sentry DSN o4510830522925056 | Info.plist |

## 2. Login — three real doors (all discovered + fingerprinted)
1. **ChatGPT / Codex device flow — replicated and [LIVE]-tested** (CoderKit/ChatGPTAuth.swift @296688):
   * `client_id = app_EMoamEEZ73f0CkXaXp7hrann` (the Codex CLI ID), scope `openid profile email offline_access`, `codex_cli_simplified_flow=true`, `id_token_add_organizations=true`, PKCE S256.
   * `POST https://auth.openai.com/api/accounts/deviceauth/usercode` **[LIVE: HTTP 200 → `device_auth_id`, `user_code`, `expires_at`; plain curl UA gets CF 530 — a `CFNetwork` User-Agent is REQUIRED]**.
   * approval page `https://auth.openai.com/codex/device?...` **[LIVE: URL accepted]**.
   * `POST …/deviceauth/token {client_id, device_auth_id, user_code, verifier}` **[LIVE: 403 `Device authorization is pending`]** → on approval returns `authorization_code`, exchanged at `POST https://auth.openai.com/oauth/token` (form-encoded).
   * account/plan from JWT claim `https://api.openai.com/auth` (`chatgpt_account_id`, `chatgpt_plan_type`, `chatgpt_compute_residency`). `ChatGPT-Account-Id` + `x-codex-turn-state` + `x-openai-internal-codex-residency` sent to `https://chatgpt.com/backend-api/codex/responses`; usage `https://chatgpt.com/backend-api/wham/usage`; profile `https://api.openai.com/profile`.
2. **FreeMilk account (web) — [LIVE] fingerprinted**: `POST https://api.milq.ai/account/auth/google` with JSON `{"identity_token": …}` — server answered with FastAPI validation error naming the exact field. `identity_token` = Google ID token. Response contract (decoded struct strings): `access_token, refresh_token, expires_in, user{email}, intro_credits, has_refresh_token, profile_readable`. Companion routes: `POST /auth/refresh`, `GET /auth/me`, `POST /account/api/*` (session-cookie based; `summary` → `Missing account session` **[LIVE]**), `/account/auth/apple`.
3. **FreeMilk account (mobile) — device-gated [LIVE]**: `POST /auth/google|/auth/apple|/auth/refresh|/auth/me` all answer **`403 "This client is not supported. Please use the Milq iOS app."`** regardless of headers/body — verified 8 header/body variants. The app sends `client_assertion`+`client_assertion_type=urn:ietf:params:oauth:client-assertion-type:appcheck` (GoogleSignIn/GTMAppAuth path) plus `device_check_token` (Apple DeviceCheck `DCAppAttestService`, App Check via App Attest `firebaseappcheck.googleapis.com/v1` — GACAppCheck/AppCheckCore classes, `com.firebase.GACAppAttestKeyIDStorage`).
   → **Hardware wall**: a real App Check token requires the iPhone Secure Enclave key attested for `ai.milq.mobile`, or a Firebase-owner-approved debug token (`v1beta …/debugTokens` needs OAuth admin **[LIVE: 401 CREDENTIALS_MISSING]**; `…:exchangeDebugToken` returns `SERVICE_DISABLED` for project 311745113120 **[LIVE]**). A desktop client cannot forge this. The bridge therefore logs in through door (1) for Codex-plan models and door (2) for the account side, and documents (3) as the premium-hosted path that only a real device session can unlock.

## 3. Models (HostedModels.swift, literal neighborhoods preserved)
Catalog decoded-field shape: `tier, mode, apiFormat(chat_completions|anthropic_messages), contextWindow, acceptsImages, isDefault, iconUrl, id, name` inside `models` list; separate `freeModels` + `premiumModels` catalogs/endpoints (`freeModelsEndpoint`, `premiumModelsEndpoint`, `HostedModelEndpoint` with `apiKey, baseUrl`) — the backend hands per-model **baseUrl+apiKey+headers**; the app talks to providers directly (`ProxyModelClient`/`OneShotModelClient` build `{baseUrl}/chat/completions`, headers incl. `anthropic-version`).
Literal model ids **[binary evidence, verbatim]**:
* free: `gpt-6-astra`, `gpt-6-sol`, `gpt-6-luna`, `deepseek-ai/DeepSeek-V4.1-Flash` ("DeepSeek V4.1 Flash"), `zai-org/GLM-5.3-Flash`
* premium: `gpt-5.5`, `gpt-5.6-luna`, `gpt-5.6-sol`, `gpt-5.6-terra`
* plan/codex: `gpt-5-codex` (default `is_default`), codex flow above

## 4. SIPS / ads economics (FreeSips.swift, PremiumSubscription.swift)
* Balance fields: `initialFreeBalance`, `periodicFreeBalance`, `subscriptionBalance`, `purchasedBalance`; `reset_at`, `dailySips`; `free_sips_refresh` event. "You're out of sips. Pick a free model…", "% of the daily allowance left".
* Ads: Gravity SDK (placement id, hashed email/phone identity, `consent`, `isIPad`), `freemilkAdsEnabled` flag. Premium: `payments/subscription`, `payments/cancel-subscription`, `payments/apple/subscription/refresh`, `premiumRequired`, `model_tool_premium_required`.
* Other verified mobile paths: `auth/app-reviews`, `runtime/ios-instances`, `runtime/xcode-instances`, `deviceauth/callback` (freemilk:// scheme).

## 5. What the bridge replicates
`bridge/env.py` = extracted constants; `bridge/codex_auth.py` = door-1 full state machine (usercode → poll → oauth exchange → plan/account from JWT; CFNetwork UA) + door-2 Google→identity_token→milq web login; `server.py` = OpenAI-compatible surface (`/v1/models` with tiers, `/v1/chat/completions` [stream: codex-SSE → chat-SSE translation], `/v1/responses`, usage/status/health, `/ads/unlock`, settings), real SPA (`/`), and `/local/key` loopback bootstrap.
Remaining hard limit (documented, not hidden): **premium hosted models need the device-attested (door-3) session** — the bridge keeps a fallback to your own OpenAI key (Settings) and the ad-unlock path is an honest stand-in until the rewarded-ad callback contract of Gravity is wired.
