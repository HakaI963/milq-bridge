"""Replicated FreeMilk client environment (from freemilk.ipa decompile, see docs/REVERSE.md).

Every constant here was extracted from the PhoneCode Mach-O / GoogleService-Info.plist and
LIVE-VERIFIED against the real backends (deviceauth usercode -> HTTP 200, auth/google -> 405/403
contract, account/auth/google -> FastAPI validation of `identity_token`).
"""
import platform, uuid

# --- ChatGPT / Codex device-flow (CoderKit/ChatGPTAuth.swift) ---
CODEX_CLIENT_ID   = "app_EMoamEEZ73f0CkXaXp7hrann"          # binary @296688
OPENAI_AUTH_BASE  = "https://auth.openai.com"               # binary @298544
OPENAI_API_BASE   = "https://api.openai.com/v1"
OPENAI_PROFILE    = "https://api.openai.com/profile"        # account ids
OPENAI_AUTH_TOKEN = "https://api.openai.com/auth"           # chatgpt_account_id/is_fedramp/plan
CODEX_BACKEND     = "https://chatgpt.com/backend-api/codex" # responses API
WHAM_USAGE        = "https://chatgpt.com/backend-api/wham/usage"
DEVICE_USERCODE   = OPENAI_AUTH_BASE + "/api/accounts/deviceauth/usercode"
DEVICE_TOKEN      = OPENAI_AUTH_BASE + "/api/accounts/deviceauth/token"
OAUTH_TOKEN       = OPENAI_AUTH_BASE + "/oauth/token"
DEVICE_VERIFY     = OPENAI_AUTH_BASE + "/codex/device"
SCOPE             = "openid profile email offline_access"   # binary @298352

# --- Google / FreeMilk account (MilqMobile2/MilqSignIn.swift) ---
MILQ_BASE         = "https://api.milq.ai"
MILQ_ACCOUNT_URL  = MILQ_BASE + "/account"
MILQ_WEB_LOGIN    = MILQ_BASE + "/account/auth/google"      # verified: JSON {"identity_token": ...}
MILQ_MOBILE_LOGIN = MILQ_BASE + "/auth/google"              # device-gated: 403 for non-app clients
GOOGLE_IOS_CLIENT = "311745113120-qptt8h4kcsb3p2tbd8bpg3n9etvjf3km.apps.googleusercontent.com"
GOOGLE_IOS_REVERSED = "com.googleusercontent.apps.311745113120-qptt8h4kcsb3p2tbd8bpg3n9etvjf3km"
GOOGLE_WEB_CLIENT = "682587638607-0i56v9dho4f19mmhh692plr49404ms7e.apps.googleusercontent.com"
GOOGLE_API_KEY    = "AIzaSyC3618xCRXx5WxNKsQVjkioTmCKlw6zZ0M"
GOOGLE_AUTH_URL   = "https://accounts.google.com/o/oauth2/v2/auth"   # binary @596496
GOOGLE_TOKEN_URL  = "https://www.googleapis.com/oauth2/v4/token"     # binary https://%@/token
GOOGLE_USERINFO   = "https://www.googleapis.com/oauth2/v3/userinfo"  # binary userinfo
BUNDLE_ID         = "ai.milq.mobile"

# --- Anthropic ---
ANTHROPIC_BASE    = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"

# --- Ads (Gravity SDK) ---
GRAVITY_AD_URL    = "https://server.trygravity.ai/api/v1/ad"

# Device identity: stable per install (the app persists device auth ids in keychain)
def load_device_state(path):
    import json, os
    if os.path.exists(path):
        try: return json.load(open(path))
        except Exception: pass
    st = {"installation_id": str(uuid.uuid4()), "first_run": platform.system()}
    return st

def cfnetwork_ua(app="PhoneCode", build="52"):
    # Matches what the iOS app sends (CFNetwork UA) — required: plain curl gets CF 530 on auth.openai.com.
    return f"{app}/{build} CFNetwork/1568 Darwin/25.0.0"

def accept_headers(extra=None):
    h = {"Accept": "application/json", "User-Agent": cfnetwork_ua()}
    if extra: h.update(extra)
    return h

# --- Hosted model catalog (from HostedModels.swift literals + mangled type metadata) ---
# JSON decoded keys: tier/mode/apiFormat/contextWindow/acceptsImages/isDefault/iconUrl/models
MODELS = [
    # FREE daily (hosted by FreeMilk, metered by SIPS; apiFormat chat_completions)
    dict(id="gpt-6-astra",                 display="GPT-6 Astra",              tier="free",    mode="agent", api_format="chat_completions",    context_window=400000, accepts_images=True,  is_default=False, backend="milq",   owned_by="freemilk"),
    dict(id="gpt-6-sol",                   display="GPT-6 Sol",                tier="free",    mode="agent", api_format="chat_completions",    context_window=400000, accepts_images=True,  is_default=False, backend="milq",   owned_by="freemilk"),
    dict(id="gpt-6-luna",                  display="GPT-6 Luna",               tier="free",    mode="agent", api_format="chat_completions",    context_window=400000, accepts_images=True,  is_default=False, backend="milq",   owned_by="freemilk"),
    dict(id="deepseek-ai/DeepSeek-V4.1-Flash", display="DeepSeek V4.1 Flash",  tier="free",    mode="agent", api_format="chat_completions",    context_window=128000, accepts_images=False, is_default=False, backend="milq",   owned_by="freemilk"),
    dict(id="zai-org/GLM-5.3-Flash",       display="GLM 5.3 Flash",            tier="free",    mode="agent", api_format="chat_completions",    context_window=128000, accepts_images=False, is_default=False, backend="milq",   owned_by="freemilk"),
    # Codex via the user's own ChatGPT sign-in (CoderKit/ChatGPTAuth.swift -> CODEX_BACKEND)
    dict(id="gpt-5-codex",                 display="GPT-5 Codex (ChatGPT plan)", tier="plan",  mode="agent", api_format="responses",         context_window=272000, accepts_images=False, is_default=True,  backend="codex",  owned_by="openai"),
    # PREMIUM (hosted; unlock with payment or rewarded ads; api_format anthropic_messages|chat_completions)
    dict(id="gpt-5.5",                     display="GPT-5.5",                  tier="premium", mode="agent", api_format="chat_completions",    context_window=400000, accepts_images=True,  is_default=False, backend="milq",   owned_by="freemilk"),
    dict(id="gpt-5.6-luna",                display="GPT-5.6 Luna",             tier="premium", mode="agent", api_format="chat_completions",    context_window=400000, accepts_images=True,  is_default=False, backend="milq",   owned_by="freemilk"),
    dict(id="gpt-5.6-sol",                 display="GPT-5.6 Sol",              tier="premium", mode="agent", api_format="chat_completions",    context_window=400000, accepts_images=True,  is_default=False, backend="milq",   owned_by="freemilk"),
    dict(id="gpt-5.6-terra",               display="GPT-5.6 Terra",            tier="premium", mode="agent", api_format="chat_completions",    context_window=400000, accepts_images=True,  is_default=False, backend="milq",   owned_by="freemilk"),
]

def model_by_id(mid):
    for m in MODELS:
        if m["id"] == mid: return m
    return None

def oai_model(m):
    # OpenAI /v1/models shape
    return {"id": m["id"], "object": "model", "created": 1760000000,
            "owned_by": m["owned_by"],
            "milq": {"tier": m["tier"], "display": m["display"], "mode": m["mode"],
                     "api_format": m["api_format"], "context_window": m["context_window"],
                     "accepts_images": m["accepts_images"], "is_default": m["is_default"],
                     "backend": m["backend"]}}
