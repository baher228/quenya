import argparse
import io
import json
import logging
import os
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx
from PIL import Image, ImageOps

from app import db, images
from app.sources import STOCK_PHRASE, is_unavailable
from app.tagging import tag_item

LOG = logging.getLogger(__name__)


def _connect():
    import psycopg
    from psycopg.rows import dict_row

    dsn = os.getenv("SUPABASE_DB_URL")
    if not dsn:
        raise RuntimeError("SUPABASE_DB_URL is required")
    return psycopg.connect(dsn, row_factory=dict_row, prepare_threshold=None)


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


def _download_original_image(url: str) -> bytes:
    response = httpx.get(
        url,
        headers={"User-Agent": images.BROWSER_UA},
        follow_redirects=True,
        timeout=15,
    )
    response.raise_for_status()
    content = response.content
    if len(content) <= 3 * 1024:
        raise ValueError("Image download is 3 KB or smaller")
    with Image.open(io.BytesIO(content)) as image:
        image.verify()
    return content


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


def _is_stock_row(row: dict[str, Any], record: dict[str, Any] | None = None) -> bool:
    if record is None:
        record = row.get("record")
        if isinstance(record, str):
            try:
                record = json.loads(record)
            except json.JSONDecodeError:
                record = None
    if not isinstance(record, dict):
        record = {}
    availability = row.get("availability") or record.get("availability")
    if is_unavailable(availability):
        return True
    return any(
        isinstance(title, str) and bool(STOCK_PHRASE.fullmatch(title.strip()))
        for title in (row.get("title"), record.get("title"))
    )


def run_insert_only_file_import(
    paths: list[str | os.PathLike[str]],
    connection: Any,
    known_urls: set[str],
    *,
    max_new_items: int | None = None,
    tag_failure_streak: int = 0,
    max_tag_failure_streak: int = 20,
) -> dict[str, Any]:
    """Tag first and insert only into quenya.items without running schema setup."""
    inserted = tagged = skipped = skipped_stock = tag_failed = failed = 0
    inserted_urls: list[str] = []
    for path in paths:
        with open(path, encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid JSON in {path}:{line_number}") from exc
                if not isinstance(row, dict):
                    raise ValueError(f"Expected an object in {path}:{line_number}")
                url = str(row.get("url") or "")
                if not url or url in known_urls:
                    skipped += 1
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
                if _is_stock_row(row, record):
                    skipped += 1
                    skipped_stock += 1
                    continue
                image_url = row.get("image_url_original") or record.get("image_url")
                try:
                    price = float(row.get("price_gbp", record.get("price_gbp")))
                except (TypeError, ValueError):
                    failed += 1
                    continue
                currency = str(row.get("currency") or record.get("currency") or "GBP").upper()
                if (
                    not image_url
                    or not str(image_url).startswith(("http://", "https://"))
                    or price <= 0
                    or currency not in {"GBP", "£"}
                ):
                    failed += 1
                    continue
                try:
                    content = _download_original_image(image_url)
                    local_image = _cache_image(image_url, content)
                    image_path = local_image
                    if images.storage_enabled():
                        storage_path = f"{images.image_key(image_url)}.jpg"
                        with open(local_image, "rb") as image_file:
                            images.upload_storage_object(
                                storage_path, image_file.read(), "image/jpeg"
                            )
                        image_path = storage_path
                except (httpx.HTTPError, OSError, RuntimeError, ValueError):
                    failed += 1
                    continue
                item = dict(record)
                item.update(
                    id=str(record.get("id") or ""),
                    source=str(row.get("source") or record.get("source") or ""),
                    source_url=url,
                    title=str(row.get("title") or record.get("title") or ""),
                    brand=row.get("brand") or record.get("brand"),
                    price_gbp=price,
                    description=record.get("description"),
                    image_url=image_url,
                    image_path=image_path,
                    gender_hint=row.get("gender") or record.get("gender_hint"),
                    condition_hint=row.get("condition") or record.get("condition_hint"),
                    availability=row.get("availability") or record.get("availability"),
                    query=record.get("query"),
                    fetched_at=datetime.now(UTC).isoformat(),
                )
                try:
                    tags, meta = tag_item(item, log_attempts=False)
                except Exception as exc:
                    tag_failed += 1
                    tag_failure_streak += 1
                    if tag_failure_streak >= max_tag_failure_streak:
                        return {
                            "inserted": inserted, "tagged": tagged, "skipped": skipped,
                            "skipped_out_of_stock": skipped_stock, "tag_failed": tag_failed,
                            "failed": failed, "tag_failure_streak": tag_failure_streak,
                            "imports_paused": True,
                            "pause_reason": f"{type(exc).__name__}: {exc}",
                            "inserted_urls": inserted_urls,
                        }
                    continue
                tag_failure_streak = 0
                item["image_path"] = image_path
                raw_json = record.get("raw_json", record)
                if not isinstance(raw_json, str):
                    raw_json = json.dumps(raw_json, ensure_ascii=False)
                try:
                    cursor = connection.execute(
                        """INSERT INTO quenya.items (
                            id, source, source_url, title, brand, price_gbp, size,
                            description, image_url, image_path, gender_hint, condition_hint,
                            availability, query, fetched_at, raw_json, tags_json,
                            tags_request_id, tagged_at
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s, %s
                        ) ON CONFLICT (source_url) DO NOTHING RETURNING source_url""",
                        (
                            item["id"], item["source"], item["source_url"], item["title"],
                            item.get("brand"), item["price_gbp"], item.get("size"),
                            item.get("description"), item["image_url"], item["image_path"],
                            item.get("gender_hint"), item.get("condition_hint"),
                            item.get("availability"), item.get("query"), item["fetched_at"],
                            raw_json, json.dumps(tags.model_dump(mode="json"), ensure_ascii=False),
                            meta.request_id, datetime.now(UTC).isoformat(),
                        ),
                    )
                    inserted_row = cursor.fetchone()
                    connection.commit()
                except Exception as exc:
                    try:
                        connection.rollback()
                    except Exception:
                        LOG.exception("Rollback failed after item insert error url=%s", url)
                        raise
                    failed += 1
                    LOG.error(
                        "Item insert failed source=%s url=%s reason=%s: %s",
                        item["source"], url, type(exc).__name__, exc,
                    )
                    continue
                known_urls.add(url)
                if inserted_row:
                    inserted += 1
                    tagged += 1
                    inserted_urls.append(url)
                    if max_new_items is not None and inserted >= max_new_items:
                        return {
                            "inserted": inserted, "tagged": tagged, "skipped": skipped,
                            "skipped_out_of_stock": skipped_stock, "tag_failed": tag_failed,
                            "failed": failed, "tag_failure_streak": tag_failure_streak,
                            "imports_paused": False, "pause_reason": None,
                            "inserted_urls": inserted_urls,
                        }
                else:
                    skipped += 1
                    known_urls.add(url)
    return {
        "inserted": inserted, "tagged": tagged, "skipped": skipped,
        "skipped_out_of_stock": skipped_stock, "tag_failed": tag_failed,
        "failed": failed, "tag_failure_streak": tag_failure_streak,
        "imports_paused": False, "pause_reason": None, "inserted_urls": inserted_urls,
    }


def _import_rows(rows: list[dict[str, Any]], no_tag: bool) -> dict[str, int]:
    known_urls = db.source_urls()
    inserted = tagged = skipped = tag_failed = failed = 0
    completed: list[str] = []
    for row in rows:
        url = row.get("url")
        if not url:
            failed += 1
            continue
        if url in known_urls:
            skipped += 1
            if "image_path" in row:
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
        if _is_stock_row(row, record):
            skipped += 1
            if "image_path" in row:
                completed.append(url)
            continue
        try:
            file_import = bool(row.get("_file_import"))
            image_url = (
                row.get("image_url_original") or record.get("image_url")
                if file_import
                else record.get("image_url") or row.get("image_url_original")
            )
            if not image_url:
                failed += 1
                continue
            if file_import:
                local_image = _cache_image(
                    image_url, _download_original_image(image_url)
                )
            else:
                storage_path = row.get("image_path") or record.get("image_path")
                if not storage_path:
                    failed += 1
                    continue
                local_image = images.image_path(image_url)
                if not local_image.exists():
                    _cache_image(image_url, _download_storage(storage_path))
                local_image = str(local_image)
            record = dict(record)
            if file_import:
                record.update(
                    source=row.get("source") or record.get("source"),
                    source_url=url,
                    title=row.get("title") or record.get("title"),
                    brand=row.get("brand") or record.get("brand"),
                    price_gbp=row.get("price_gbp", record.get("price_gbp")),
                    gender_hint=row.get("gender") or record.get("gender_hint"),
                    condition_hint=row.get("condition") or record.get("condition_hint"),
                )
            record["image_url"] = image_url
            record["image_path"] = local_image
            added = db.insert_items([record])
            if not added:
                skipped += 1
                if "image_path" in row:
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
            if "image_path" in row:
                completed.append(url)
        except Exception:
            failed += 1
    if completed:
        _mark_imported(completed)
    return {
        "fetched": len(rows), "inserted": inserted, "tagged": tagged,
        "skipped": skipped, "tag_failed": tag_failed, "failed": failed,
    }


def run_import(limit: int | None = None, no_tag: bool = False) -> dict[str, int]:
    return _import_rows(_pending_rows(limit), no_tag)


def run_file_import(
    paths: list[str | os.PathLike[str]],
    per_shop: int | None = None,
    exclude_sources: tuple[str, ...] | list[str] = (),
    no_tag: bool = False,
) -> dict[str, int]:
    if per_shop is not None and per_shop < 0:
        raise ValueError("per_shop must be non-negative")
    excluded = {source.strip().lower() for source in exclude_sources}
    known_urls = db.source_urls()
    selected: list[dict[str, Any]] = []
    selected_per_shop: dict[str, int] = {}
    for path in paths:
        with open(path, encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid JSON in {path}:{line_number}") from exc
                if not isinstance(row, dict):
                    raise ValueError(f"Expected an object in {path}:{line_number}")
                source = str(row.get("source") or "").strip()
                if source.lower() in excluded:
                    continue
                url = row.get("url")
                if url not in known_urls and not _is_stock_row(row):
                    count = selected_per_shop.get(source, 0)
                    if per_shop is not None and count >= per_shop:
                        continue
                    selected_per_shop[source] = count + 1
                row["_file_import"] = True
                selected.append(row)
    return _import_rows(selected, no_tag)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int)
    parser.add_argument("--from-file", nargs="+")
    parser.add_argument("--per-shop", type=int)
    parser.add_argument("--exclude-source", action="append", default=[])
    parser.add_argument("--no-tag", action="store_true")
    args = parser.parse_args()
    if args.from_file:
        result = run_file_import(
            args.from_file, args.per_shop, args.exclude_source, args.no_tag
        )
    else:
        result = run_import(args.limit, args.no_tag)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
