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
