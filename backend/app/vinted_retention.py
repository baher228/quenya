"""Delete aged or unavailable Vinted catalogue rows (and local image copies)."""

from __future__ import annotations

import argparse
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app import db, images, media_policy

logger = logging.getLogger(__name__)


def _cutoff_iso(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


def _delete_local_image(item: dict[str, Any]) -> bool:
    removed = False
    image_path = item.get("image_path")
    if image_path:
        path = Path(str(image_path))
        if path.is_file():
            path.unlink()
            removed = True
        elif not path.is_absolute():
            # Relative storage key — leave object storage to purge_vinted_images.
            pass
    image_url = item.get("image_url")
    if image_url:
        cached = images.image_path(image_url)
        if cached.is_file():
            cached.unlink()
            removed = True
    return removed


def run_retention(
    *,
    days: int | None = None,
    limit: int = 5000,
    dry_run: bool = False,
    clear_raw_json: bool = False,
) -> dict[str, Any]:
    retention_days = days if days is not None else media_policy.vinted_retention_days()
    cutoff = _cutoff_iso(retention_days)
    candidates = db.vinted_items_for_retention(
        older_than_iso=cutoff, limit=limit
    )
    local_removed = 0
    raw_cleared = 0
    if clear_raw_json and not dry_run:
        raw_cleared = db.clear_vinted_raw_json()
    if not dry_run:
        for item in candidates:
            if _delete_local_image(item):
                local_removed += 1
        deleted = db.delete_items([item["id"] for item in candidates])
    else:
        deleted = 0
    return {
        "dry_run": dry_run,
        "retention_days": retention_days,
        "cutoff": cutoff,
        "matched": len(candidates),
        "deleted": deleted if not dry_run else 0,
        "would_delete": len(candidates) if dry_run else deleted,
        "local_images_removed": local_removed,
        "raw_json_cleared": raw_cleared,
        "sample_ids": [item["id"] for item in candidates[:20]],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Delete Vinted items older than N days or marked unavailable"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Override QUENYA_VINTED_RETENTION_DAYS (default 14)",
    )
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List matches without deleting",
    )
    parser.add_argument(
        "--clear-raw-json",
        action="store_true",
        help="Also null/empty raw_json on remaining Vinted rows",
    )
    args = parser.parse_args(argv)
    if args.days is not None and args.days < 1:
        parser.error("--days must be >= 1")
    if args.limit < 1:
        parser.error("--limit must be >= 1")
    result = run_retention(
        days=args.days,
        limit=args.limit,
        dry_run=args.dry_run,
        clear_raw_json=args.clear_raw_json,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
