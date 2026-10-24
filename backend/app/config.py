import os
from pathlib import Path

from dotenv import load_dotenv

from app.prompts import PROMPT_VERSION

__all__ = ["PROMPT_VERSION"]

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / ".env")

MUBIT_API_KEY = os.getenv("MUBIT_API_KEY", "")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
MUBIT_BASE_URL = os.getenv("MUBIT_BASE_URL", "https://api.mubit.ai/openai/v1")
DB_PATH = Path(os.getenv("DB_PATH", str(BACKEND_ROOT / "data" / "quenya.db"))).expanduser()

_DEFAULT_CONTEXT_SOURCES = ["vinted", "asos.com", "uniqlo.com"]
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
    "smart": _DEFAULT_CONTEXT_SOURCES,
    "evening": _DEFAULT_CONTEXT_SOURCES,
    "lounge": _DEFAULT_CONTEXT_SOURCES,
    "other": _DEFAULT_CONTEXT_SOURCES,
}
SPORT_CONTEXTS = frozenset({"gym", "running", "swim", "outdoor"})
SPORT_IMAGE_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
