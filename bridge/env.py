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

# ---------------- ALT free providers (bridge-added; verified reachable 2026-11-04) ----------------
# pollinations: keyless anonymous tier, OpenAI-compatible (live-tested HTTP 200).
# The rest need a FREE browser signup key (no card) and are surfaced only when a key is present.
ALT_PROVIDERS = {
    "pollinations": {"base": "https://text.pollinations.ai/openai", "key_env": None, "auth": "none", "path": "",
                     "models": [{"id": "openai-fast", "name": "GPT-OSS 20B — keyless free", "ctx": 128000}]},
    "cerebras": {"base": "https://api.cerebras.ai/v1", "key_env": "CEREBRAS_API_KEY", "auth": "bearer",
                 "models": [{"id": "cerebras:qwen-3-235b", "name": "Qwen3 235B (ultra-fast)", "ctx": 128000},
                            {"id": "cerebras:llama-3.1-8b", "name": "Llama 3.1 8B", "ctx": 128000}]},
    "deepinfra": {"base": "https://api.deepinfra.com/v1/openai", "key_env": "DEEPINFRA_API_KEY", "auth": "bearer",
                  "models": [{"id": "deepinfra:deepseek-ai/DeepSeek-V3", "name": "DeepSeek V3", "ctx": 128000},
                             {"id": "deepinfra:meta-llama/Llama-3.3-70B-Instruct", "name": "Llama 3.3 70B", "ctx": 128000}]},
    "groq": {"base": "https://api.groq.com/openai/v1", "key_env": "GROQ_API_KEY", "auth": "bearer",
             "models": [{"id": "groq:gpt-oss-120b", "name": "GPT-OSS 120B", "ctx": 128000},
                        {"id": "groq:llama-3.3-70b-versatile", "name": "Llama 3.3 70B", "ctx": 128000}]},
    "openrouter": {"base": "https://openrouter.ai/api/v1", "key_env": "OPENROUTER_API_KEY", "auth": "bearer",
                   "models": [{"id": "openrouter:deepseek/deepseek-chat-v3.1:free", "name": "DeepSeek V3.1 :free", "ctx": 164000},
                              {"id": "openrouter:z-ai/glm-4.5-air:free", "name": "GLM 4.5 Air :free", "ctx": 128000}]},
    "google": {"base": "https://generativelanguage.googleapis.com/v1beta/openai", "key_env": "GOOGLE_API_KEY", "auth": "bearer",
               "models": [{"id": "google:gemini-2.0-flash", "name": "Gemini 2.0 Flash", "ctx": 1000000}]},
}

def alt_models(available_keys):
    """Model dicts for providers whose key is present (pollinations always)."""
    out = []
    for prov, cfg in ALT_PROVIDERS.items():
        env_key = cfg.get("key_env")
        if env_key and not available_keys.get(env_key):
            continue
        for m in cfg["models"]:
            out.append({"id": m["id"], "object": "model", "created": 1760000000,
                        "owned_by": prov,
                        "milq": {"tier": "free", "display": m["name"], "mode": "agent",
                                 "api_format": "chat_completions", "context_window": m["ctx"],
                                 "accepts_images": False, "is_default": False,
                                 "backend": "alt:" + prov}})
    return out

# ---------------- Sqim (Milq's own desktop CLI service) — web login, NO attestation ----------------
# Discovered 2026-11-04 via github.com/milq-ai (homebrew-tap -> sqim-cli-releases Mach-O strings).
SQIM_BASE        = "https://api.sqim.dev"
SQIM_LOGIN_URL   = SQIM_BASE + "/auth/login"
SQIM_EXCHANGE    = SQIM_BASE + "/auth/exchange"
SQIM_REFRESH     = SQIM_BASE + "/auth/refresh"
SQIM_CODES       = SQIM_BASE + "/sqim/auth/codes"
SQIM_BUILDS      = SQIM_BASE + "/sqim/builds"
SQIM_RUNTIME_XCODE = SQIM_BASE + "/runtime/xcode-instances"
SQIM_GIS_CLIENT  = "964988081476-nsunhd7cqpjf58no2m6igcvscuv1ddeq.apps.googleusercontent.com"
