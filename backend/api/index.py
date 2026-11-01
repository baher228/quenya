import os

# Migrations run only from the VPS deploy step; never from a Vercel cold start.
os.environ["QUENYA_AUTO_MIGRATE"] = "0"

from app.main import app  # noqa: E402

__all__ = ["app"]
