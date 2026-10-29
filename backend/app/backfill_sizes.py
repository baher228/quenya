import argparse
import json
from typing import Any

from app import db
from app.sizes import listing_size_from_label


def _size_from_raw_json(raw_json: Any) -> str | None:
    if isinstance(raw_json, str):
        try:
            raw_json = json.loads(raw_json)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw_json, dict):
        return None
    item_box = raw_json.get("item_box")
    if not isinstance(item_box, dict):
        return None
    label = item_box.get("accessibility_label")
    return listing_size_from_label(str(label or ""))


def backfill(
    *, dry_run: bool = False, batch_size: int = 500
) -> dict[str, int]:
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    db.init_db()
    scanned = 0
    found = 0
    updated = 0
    last_id = None
    with db.db_connection() as connection:
        while True:
            query = (
                "SELECT id, raw_json FROM items "
                "WHERE source='vinted' AND (size IS NULL OR TRIM(size)='')"
            )
            params: list[Any] = []
            if last_id is not None:
                query += " AND id > ?"
                params.append(last_id)
            query += " ORDER BY id LIMIT ?"
            params.append(batch_size)
            rows = connection.execute(query, params).fetchall()
            if not rows:
                break
            scanned += len(rows)
            last_id = rows[-1]["id"]
            for row in rows:
                size = _size_from_raw_json(row["raw_json"])
                if not size:
                    continue
                found += 1
                if dry_run:
                    continue
                cursor = connection.execute(
                    "UPDATE items SET size=? WHERE id=? AND source='vinted' "
                    "AND (size IS NULL OR TRIM(size)='')",
                    (size, row["id"]),
                )
                updated += max(cursor.rowcount, 0)
    if updated:
        db.invalidate_live_catalogue()
    return {"scanned": scanned, "found": found, "updated": updated}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args(argv)
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    counts = backfill(dry_run=args.dry_run, batch_size=args.batch_size)
    mode = "dry run" if args.dry_run else "updated"
    print(
        f"Scanned {counts['scanned']} Vinted rows; found {counts['found']} sizes; "
        f"{mode} {counts['updated']} rows."
    )


if __name__ == "__main__":
    main()
