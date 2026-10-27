import os
from pathlib import Path

from dotenv import load_dotenv

from app.prompts import PROMPT_VERSION

__all__ = ["PROMPT_VERSION"]

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / ".env")

# Shared-core is archived and off by default.
# Each board learns only from its own brief and swipes.
SHARED_CORE_ENABLED = (
    os.getenv("QUENYA_SHARED_CORE", "").lower() in {"1", "true", "yes"}
)

QUENYA_LLM_PROVIDER = os.getenv("QUENYA_LLM_PROVIDER", "cursor")
QUENYA_LLM_API_KEY = os.getenv("QUENYA_LLM_API_KEY", "")
QUENYA_LLM_BASE_URL = os.getenv(
    "QUENYA_LLM_BASE_URL", "https://api.openai.com/v1"
)
QUENYA_LLM_DAILY_CAP = os.getenv("QUENYA_LLM_DAILY_CAP", "300")
QUENYA_TAGGER = os.getenv("QUENYA_TAGGER", "local")
QUENYA_CORS_ORIGINS = os.getenv(
    "QUENYA_CORS_ORIGINS", "http://localhost:5173"
)
QUENYA_APP_URL = os.getenv("QUENYA_APP_URL", "https://app.quenya.co.uk").rstrip("/")
QUENYA_CATALOGUE_TTL_S = int(os.getenv("QUENYA_CATALOGUE_TTL_S", "300"))
QUENYA_WARM_CACHES = os.getenv("QUENYA_WARM_CACHES", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
QUENYA_IMAGE_MAX_SIDE = int(os.getenv("QUENYA_IMAGE_MAX_SIDE", "1600"))
QUENYA_IMAGE_QUALITY = int(os.getenv("QUENYA_IMAGE_QUALITY", "85"))
QUENYA_IMAGE_STORE = os.getenv("QUENYA_IMAGE_STORE", "supabase").strip().lower()
QUENYA_R2_ACCOUNT_ID = os.getenv("QUENYA_R2_ACCOUNT_ID", "")
QUENYA_R2_ACCESS_KEY_ID = os.getenv("QUENYA_R2_ACCESS_KEY_ID", "")
QUENYA_R2_SECRET_ACCESS_KEY = os.getenv("QUENYA_R2_SECRET_ACCESS_KEY", "")
QUENYA_R2_BUCKET = os.getenv("QUENYA_R2_BUCKET", "quenya")
QUENYA_R2_PUBLIC_URL = os.getenv("QUENYA_R2_PUBLIC_URL", "").rstrip("/")
CURSOR_API_KEY = os.getenv("CURSOR_API_KEY", "")
QUENYA_CURSOR_MODEL = os.getenv(
    "QUENYA_CURSOR_MODEL", "gemini-3.7-flash-high"
)
QUENYA_CURSOR_STYLIST_MODEL = os.getenv(
    "QUENYA_CURSOR_STYLIST_MODEL", "gpt-5.3-codex-low-fast"
)
QUENYA_CURSOR_BIN = os.getenv("QUENYA_CURSOR_BIN", "")
QUENYA_CURSOR_TIMEOUT_S = os.getenv("QUENYA_CURSOR_TIMEOUT_S", "120")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
DB_PATH = Path(os.getenv("DB_PATH", str(BACKEND_ROOT / "data" / "quenya.db"))).expanduser()

SHOP_TIERS = {
    "ssense.com": "luxury",
    "arket.com": "premium",
    "cos.com": "premium",
    "stories.com": "premium",
    "barbour.com": "premium",
    "charlestyrwhitt.com": "premium",
    "johnsmedley.com": "luxury",
    "burberry.com": "luxury",
    "stoneisland.com": "luxury",
    "ralphlauren.co.uk": "premium",
    "hugoboss.com": "premium",
    "lacoste.com": "premium",
    "mango.com": "high_street",
    "thenorthface.com": "high_street",
    "uniqlo.com": "high_street",
    "urbanoutfitters.com": "high_street",
    "asos.com": "high_street",
}
_DEFAULT_CONTEXT_SOURCES = [
    "vinted", "asos.com", "uniqlo.com", "arket.com", "mango.com",
    "urbanoutfitters.com", "ssense.com",
]
CONTEXT_SOURCES = {
    "gym": [
        "gymshark.com", "nike.com", "underarmour.co.uk", "lululemon.co.uk",
        "decathlon.co.uk", "sportsdirect.com",
    ],
    "running": [
        "nike.com", "decathlon.co.uk", "underarmour.co.uk", "lululemon.co.uk",
        "sportsdirect.com",
    ],
    "swim": ["decathlon.co.uk", "sportsdirect.com", "vinted", "asos.com"],
    "outdoor": [
        "cotswoldoutdoor.com", "arcteryx.com", "montane.com", "rab.equipment",
        "ellis-brigham.com", "decathlon.co.uk", "patagonia.com", "vinted",
    ],
    "city": _DEFAULT_CONTEXT_SOURCES,
    "workwear": _DEFAULT_CONTEXT_SOURCES,
    "smart": ["vinted", "ssense.com", "arket.com", "mango.com", "asos.com"],
    "evening": ["vinted", "ssense.com", "arket.com", "mango.com", "asos.com"],
    "lounge": _DEFAULT_CONTEXT_SOURCES,
    "other": _DEFAULT_CONTEXT_SOURCES,
}
SPORT_CONTEXTS = frozenset({"gym", "running", "swim", "outdoor"})
SPORT_IMAGE_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
