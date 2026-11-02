#!/usr/bin/env python3
"""Tag the local deck catalogue with the app's real LLM tagger in parallel shards."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
os.environ["PATH"] = f"{Path.home() / '.local/bin'}:{os.environ['PATH']}"
os.environ.setdefault("QUENYA_TAGGER", "llm")
os.environ.setdefault("QUENYA_LLM_DAILY_CAP", "5000")

from app import db  # noqa: E402
from app.tag import tag_untagged  # noqa: E402

SHARDS = int(os.getenv("SHARDS", "4"))
SHARD = int(os.getenv("SHARD", "0"))


def main() -> None:
    """The LLM client allows 4 concurrent calls per process, so run one process per SHARD."""
    db.init_db()
    with db.db_connection() as connection:
        ids = [
            row["id"] for row in connection.execute(
                "SELECT id FROM items WHERE tags_json IS NULL ORDER BY id"
            ).fetchall()
        ]
    shard = ids[SHARD::SHARDS]
    print(f"untagged: {len(ids)}; shard {SHARD}/{SHARDS}: {len(shard)}", flush=True)
    result = tag_untagged(len(shard) or 1, 4, None, True, shard)
    print(json.dumps({k: result[k] for k in ("selected", "ok", "failed")}), flush=True)


if __name__ == "__main__":
    main()
