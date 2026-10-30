import argparse
from typing import Any

from app import db, images


def _display_image(image_url: str) -> bytes:
    if images.display_storage_enabled():
        return images.download_display_object(
            f"{images.image_key(image_url)}.jpg"
        )
    return images.image_path(image_url).read_bytes()


def backfill(
    *, dry_run: bool = False, batch_size: int = 500, limit: int | None = None
) -> dict[str, int]:
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    db.init_db()
    scanned = built = failed = updated = 0
    last_id = None
    with db.db_connection() as connection:
        while limit is None or scanned < limit:
            query = "SELECT id, image_url FROM items WHERE image_preview IS NULL"
            params: list[Any] = []
            if last_id is not None:
                query += " AND id > ?"
                params.append(last_id)
            query += " ORDER BY id LIMIT ?"
            params.append(
                min(batch_size, limit - scanned) if limit is not None else batch_size
            )
            rows = connection.execute(query, params).fetchall()
            if not rows:
                break
            last_id = rows[-1]["id"]
            for row in rows:
                scanned += 1
                try:
                    preview = images.preview_data_url(_display_image(row["image_url"]))
                except Exception:
                    failed += 1
                    continue
                built += 1
                if not dry_run:
                    cursor = connection.execute(
                        "UPDATE items SET image_preview=? "
                        "WHERE id=? AND image_preview IS NULL",
                        (preview, row["id"]),
                    )
                    updated += max(cursor.rowcount, 0)
    if updated:
        db.invalidate_live_catalogue()
    return {
        "scanned": scanned,
        "built": built,
        "failed": failed,
        "updated": updated,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args(argv)
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    counts = backfill(
        dry_run=args.dry_run,
        batch_size=args.batch_size,
        limit=args.limit,
    )
    print(
        f"Scanned {counts['scanned']}; built {counts['built']}; "
        f"failed {counts['failed']}; updated {counts['updated']}"
    )


if __name__ == "__main__":
    main()
