"""Purge previously re-hosted Vinted display images from R2/Supabase.

Dry-run by default. Pass --execute to delete objects whose keys still match
Vinted rows (or orphaned keys recorded on Vinted items' image_path).
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

from app import db, images

logger = logging.getLogger(__name__)


def _object_key_for_item(item: dict[str, Any]) -> str | None:
    image_path = item.get("image_path")
    if image_path:
        name = Path(str(image_path)).name
        if name.endswith(".jpg"):
            return name
        if len(name) == 40 and all(char in "0123456789abcdef" for char in name):
            return f"{name}.jpg"
    image_url = item.get("image_url")
    if image_url:
        return f"{images.image_key(image_url)}.jpg"
    return None


def list_vinted_image_keys(limit: int = 20000) -> list[tuple[str, str]]:
    """Return (item_id, object_key) for Vinted rows that may have been re-hosted."""
    db.init_db()
    with db.db_connection() as connection:
        rows = connection.execute(
            """SELECT id, image_url, image_path FROM items
               WHERE source='vinted'
               ORDER BY fetched_at
               LIMIT ?""",
            (limit,),
        ).fetchall()
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        item = dict(row)
        key = _object_key_for_item(item)
        if not key or key in seen:
            continue
        seen.add(key)
        pairs.append((item["id"], key))
    return pairs


def run_purge(*, execute: bool = False, limit: int = 20000) -> dict[str, Any]:
    pairs = list_vinted_image_keys(limit=limit)
    deleted = 0
    errors: list[str] = []
    if execute:
        if not images.display_storage_enabled():
            return {
                "execute": True,
                "matched": len(pairs),
                "deleted": 0,
                "errors": ["display storage is not configured"],
                "sample_keys": [key for _item_id, key in pairs[:20]],
            }
        for _item_id, key in pairs:
            try:
                images.delete_display_object(key)
                deleted += 1
            except Exception as exc:
                errors.append(f"{key}: {type(exc).__name__}: {exc}")
    return {
        "execute": execute,
        "dry_run": not execute,
        "matched": len(pairs),
        "deleted": deleted,
        "errors": errors[:20],
        "sample_keys": [key for _item_id, key in pairs[:20]],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Purge re-hosted Vinted images from object storage (dry-run default)"
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually delete objects (default is dry-run)",
    )
    parser.add_argument("--limit", type=int, default=20000)
    args = parser.parse_args(argv)
    if args.limit < 1:
        parser.error("--limit must be >= 1")
    result = run_purge(execute=args.execute, limit=args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get("errors") and args.execute else 0


if __name__ == "__main__":
    raise SystemExit(main())
