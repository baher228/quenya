import argparse
import json
import logging
import os
import re
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path
from threading import Lock, Semaphore
from urllib.parse import urlsplit

import httpx
from PIL import Image, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import images
from app.config import BACKEND_ROOT
from app.sources import is_unavailable

LOG = logging.getLogger("quenya.image_backfill")
_HOST_LOCK = Lock()
_HOST_SEMAPHORES: dict[str, Semaphore] = {}
_IMAGE_KEY_RE = re.compile(r"^[0-9a-f]{40}\.jpg$")
_COUNT_FIELDS = ("upgraded", "same_size", "kept_old", "missing", "failed")


def _host_semaphore(url: str) -> Semaphore:
    host = (urlsplit(url).hostname or "unknown").lower()
    with _HOST_LOCK:
        return _HOST_SEMAPHORES.setdefault(host, Semaphore(2))


def _image_dimensions(content: bytes) -> tuple[int, int]:
    with Image.open(BytesIO(content)) as original:
        image = ImageOps.exif_transpose(original)
        return image.size


def _image_key(item: dict) -> str:
    stored_path = Path(str(item.get("image_path") or "")).name
    if _IMAGE_KEY_RE.fullmatch(stored_path):
        return stored_path
    return f"{images.image_key(str(item.get('image_url') or ''))}.jpg"


def _raw_primary_image(raw_json) -> str | None:
    if isinstance(raw_json, str):
        try:
            raw_json = json.loads(raw_json)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw_json, dict):
        return None
    containers = [raw_json]
    for name in ("product", "shopify_product"):
        nested = raw_json.get(name)
        if isinstance(nested, dict):
            containers.append(nested)
    for container in containers:
        primary = container.get("image")
        if isinstance(primary, dict):
            primary = primary.get("src") or primary.get("url")
        if isinstance(primary, str) and primary.startswith(("http://", "https://")):
            return primary
        images_list = container.get("images")
        if isinstance(images_list, list) and images_list:
            first = images_list[0]
            if isinstance(first, dict):
                first = first.get("src") or first.get("url")
            if isinstance(first, str) and first.startswith(("http://", "https://")):
                return first
    return None


def _best_image_url(item: dict) -> str:
    return _raw_primary_image(item.get("raw_json")) or str(item.get("image_url") or "")


def _download_source(item: dict, image_url: str) -> bytes:
    upscaled = images.upscale_image_url(image_url)
    candidates = [upscaled]
    if upscaled != image_url:
        candidates.append(image_url)
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            with _host_semaphore(candidate):
                return images.download_image_candidate(
                    candidate,
                    referer=item.get("source_url"),
                    minimum_bytes=3 * 1024,
                )
        except (httpx.HTTPError, OSError, ValueError, RuntimeError) as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise ValueError("No image URL was available")


def _download_old(key: str) -> tuple[bytes | None, str | None]:
    base_url = os.getenv("SUPABASE_URL", "")
    try:
        with _host_semaphore(base_url):
            return images.download_storage_object(key), None
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return None, "missing"
        return None, type(exc).__name__
    except Exception as exc:
        return None, type(exc).__name__


def process_item(item: dict, *, dry_run: bool = False) -> dict:
    item_id = str(item.get("id") or "")
    source = str(item.get("source") or "")
    key = _image_key(item)
    image_url = _best_image_url(item)
    old_content, old_error = _download_old(key)
    old_dimensions = None
    if old_content is not None:
        try:
            old_dimensions = _image_dimensions(old_content)
        except (OSError, ValueError):
            pass

    try:
        if not image_url.startswith(("http://", "https://")):
            raise ValueError("Item has no valid image URL")
        source_content = _download_source(item, image_url)
        new_content = images.encode_display_jpeg(source_content)
        new_dimensions = _image_dimensions(new_content)
        if (
            old_content is not None
            and old_dimensions is not None
            and new_dimensions[0] * new_dimensions[1]
            <= old_dimensions[0] * old_dimensions[1]
        ):
            stored_content = old_content
            stored_dimensions = old_dimensions
            status = "same_size"
        else:
            stored_content = new_content
            stored_dimensions = new_dimensions
            status = "upgraded"
    except Exception as exc:
        if old_content is not None:
            stored_content = old_content
            stored_dimensions = old_dimensions
            status = "kept_old"
        elif old_error == "missing":
            LOG.warning("Missing display image for item id=%s source=%s", item_id, source)
            return {
                "id": item_id,
                "source": source,
                "status": "missing",
                "stored_width": None,
                "stored_height": None,
                "stored_bytes": 0,
            }
        else:
            LOG.error(
                "Failed display image for item id=%s source=%s (%s)",
                item_id,
                source,
                type(exc).__name__,
            )
            return {
                "id": item_id,
                "source": source,
                "status": "failed",
                "stored_width": None,
                "stored_height": None,
                "stored_bytes": 0,
            }

    if not dry_run:
        try:
            endpoint = (
                f"https://{os.getenv('QUENYA_R2_ACCOUNT_ID', '')}"
                ".r2.cloudflarestorage.com"
            )
            with _host_semaphore(endpoint):
                images.upload_r2_object(key, stored_content, "image/jpeg")
        except Exception as exc:
            LOG.error(
                "Failed display image for item id=%s source=%s (%s)",
                item_id,
                source,
                type(exc).__name__,
            )
            status = "failed"
    return {
        "id": item_id,
        "source": source,
        "status": status,
        "stored_width": stored_dimensions[0] if stored_dimensions else None,
        "stored_height": stored_dimensions[1] if stored_dimensions else None,
        "stored_bytes": len(stored_content),
    }


def _database_items(source: str | None) -> list[dict]:
    import psycopg
    from psycopg.rows import dict_row

    dsn = os.getenv("QUENYA_DATABASE_URL") or os.getenv("SUPABASE_DB_URL")
    if not dsn:
        raise RuntimeError("QUENYA_DATABASE_URL or SUPABASE_DB_URL is required")
    connection = psycopg.connect(
        dsn,
        prepare_threshold=None,
        connect_timeout=10,
        row_factory=dict_row,
    )
    try:
        connection.execute("SET TRANSACTION READ ONLY")
        connection.execute("SET LOCAL statement_timeout = 30000")
        query = """SELECT id, source, source_url, title, image_url, image_path,
                          availability, raw_json
                   FROM quenya.items"""
        parameters = ()
        if source is not None:
            query += " WHERE source = %s"
            parameters = (source,)
        query += " ORDER BY source, id"
        rows = connection.execute(query, parameters).fetchall()
        return [
            dict(row)
            for row in rows
            if not is_unavailable(row.get("availability"))
        ]
    finally:
        connection.close()


def _load_progress(path: Path) -> dict:
    if not path.is_file():
        return {"results": {}}
    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state, dict) or not isinstance(state.get("results"), dict):
        raise ValueError("Invalid image backfill progress file")
    return state


def _save_progress(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _summary(results: list[dict]) -> dict[str, dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for result in results:
        grouped[result["source"]].append(result)
    summary = {}
    for source, rows in sorted(grouped.items()):
        counts = {field: 0 for field in _COUNT_FIELDS}
        measured = []
        for row in rows:
            counts[row["status"]] += 1
            if row["stored_width"] and row["stored_height"]:
                measured.append(row)
        summary[source] = {
            **counts,
            "average_stored_width": round(
                sum(row["stored_width"] for row in measured) / len(measured), 1
            )
            if measured
            else None,
            "average_stored_height": round(
                sum(row["stored_height"] for row in measured) / len(measured), 1
            )
            if measured
            else None,
            "average_stored_pixels": round(
                sum(row["stored_width"] * row["stored_height"] for row in measured)
                / len(measured)
            )
            if measured
            else None,
            "average_stored_kb": round(
                sum(row["stored_bytes"] for row in measured) / len(measured) / 1024,
                1,
            )
            if measured
            else None,
        }
    return summary


def run_backfill(
    *,
    workers: int = 8,
    limit: int | None = None,
    source: str | None = None,
    dry_run: bool = False,
    progress_file: Path | None = None,
) -> dict:
    if workers < 1:
        raise ValueError("--workers must be at least 1")
    if limit is not None and limit < 1:
        raise ValueError("--limit must be at least 1")
    progress_file = progress_file or (
        BACKEND_ROOT / "data" / "full_res_image_backfill.json"
    )
    state = _load_progress(progress_file)
    completed = set(state["results"])
    rows = [
        item
        for item in _database_items(source)
        if str(item.get("id") or "") not in completed
    ]
    if limit is not None:
        rows = rows[:limit]
    results = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(process_item, item, dry_run=dry_run): item for item in rows
        }
        for future in as_completed(futures):
            item = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {
                    "id": str(item.get("id") or ""),
                    "source": str(item.get("source") or ""),
                    "status": "failed",
                    "stored_width": None,
                    "stored_height": None,
                    "stored_bytes": 0,
                }
                LOG.error(
                    "Failed display image for item id=%s source=%s (%s)",
                    result["id"],
                    result["source"],
                    type(exc).__name__,
                )
            results.append(result)
            if not dry_run:
                state["results"][result["id"]] = result
                _save_progress(progress_file, state)
    return _summary(results)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill full-resolution display images")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--source")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--progress-file",
        type=Path,
        default=BACKEND_ROOT / "data" / "full_res_image_backfill.json",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        summary = run_backfill(
            workers=args.workers,
            limit=args.limit,
            source=args.source,
            dry_run=args.dry_run,
            progress_file=args.progress_file,
        )
    except Exception as exc:
        LOG.error("Backfill failed (%s)", type(exc).__name__)
        raise SystemExit(1) from None
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
