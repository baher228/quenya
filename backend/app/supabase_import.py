import argparse
import io
import json
import os
from typing import Any
from urllib.parse import quote

import httpx
from PIL import Image, ImageOps

from app import db, images
from app.tagging import tag_item


def _connect():
    import psycopg
    from psycopg.rows import dict_row

    dsn = os.getenv("SUPABASE_DB_URL")
    if not dsn:
        raise RuntimeError("SUPABASE_DB_URL is required")
    return psycopg.connect(dsn, row_factory=dict_row)


def _pending_rows(limit: int | None) -> list[dict[str, Any]]:
    sql = """SELECT url, source, title, brand, price_gbp, gender, category,
                     condition, image_path, image_url_original, record
              FROM quenya.listings
              WHERE imported_at IS NULL
              ORDER BY scraped_at"""
    params = ()
    if limit is not None:
        sql += " LIMIT %s"
        params = (limit,)
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]


def _mark_imported(urls: list[str]) -> None:
    if not urls:
        return
    with _connect() as connection, connection.cursor() as cursor:
        cursor.executemany(
            "UPDATE quenya.listings SET imported_at=now() WHERE url=%s",
            [(url,) for url in urls],
        )
        connection.commit()


def _download_storage(path: str) -> bytes:
    base_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    secret = os.getenv("SUPABASE_SECRET_KEY", "")
    if not base_url or not secret:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SECRET_KEY are required")
    response = httpx.get(
        f"{base_url}/storage/v1/object/quenya-listings/{quote(path, safe='/')}",
        headers={"Authorization": f"Bearer {secret}", "apikey": secret},
        follow_redirects=True,
        timeout=30,
    )
    response.raise_for_status()
    return response.content


def _cache_image(image_url: str, content: bytes) -> str:
    destination = images.image_path(image_url)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        with Image.open(io.BytesIO(content)) as original:
            image = ImageOps.exif_transpose(original).convert("RGB")
            image.thumbnail((512, 512), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=80, optimize=True)
        temporary = destination.with_suffix(".import.tmp")
        temporary.write_bytes(output.getvalue())
        temporary.replace(destination)
    return str(destination)


def run_import(limit: int | None = None, no_tag: bool = False) -> dict[str, int]:
    rows = _pending_rows(limit)
    known_urls = db.source_urls()
    inserted = tagged = skipped = tag_failed = failed = 0
    completed: list[str] = []
    for row in rows:
        url = row["url"]
        if url in known_urls:
            skipped += 1
            completed.append(url)
            continue
        record = row.get("record")
        if isinstance(record, str):
            try:
                record = json.loads(record)
            except json.JSONDecodeError:
                failed += 1
                continue
        if not isinstance(record, dict):
            failed += 1
            continue
        image_url = record.get("image_url") or row.get("image_url_original")
        storage_path = row.get("image_path") or record.get("image_path")
        if not image_url or not storage_path:
            failed += 1
            continue
        try:
            local_image = images.image_path(image_url)
            if not local_image.exists():
                _cache_image(image_url, _download_storage(storage_path))
            record["image_url"] = image_url
            record["image_path"] = str(local_image)
            added = db.insert_items([record])
            if not added:
                skipped += 1
                completed.append(url)
                known_urls.add(url)
                continue
            inserted += 1
            known_urls.add(url)
            if not no_tag:
                item = db.get_item(record["id"])
                try:
                    tags, meta = tag_item(item)
                    db.save_tags(
                        item["id"], tags.model_dump(), meta.request_id, item.get("image_path")
                    )
                    tagged += 1
                except Exception as exc:
                    db.save_tag_error(record["id"], str(exc))
                    tag_failed += 1
            completed.append(url)
        except Exception:
            failed += 1
    _mark_imported(completed)
    return {
        "fetched": len(rows), "inserted": inserted, "tagged": tagged,
        "skipped": skipped, "tag_failed": tag_failed, "failed": failed,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int)
    parser.add_argument("--no-tag", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run_import(args.limit, args.no_tag), indent=2))


if __name__ == "__main__":
    main()
