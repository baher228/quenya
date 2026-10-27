#!/usr/bin/env python3
"""Seed a local Quenya catalogue using read-only production GET requests."""

from __future__ import annotations

import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

import httpx

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))

PRODUCTION = "https://quenya-rho.vercel.app"
PRODUCT_QUERIES = (
    "jacket", "coat", "t-shirt", "shirt", "polo", "jumper", "hoodie",
    "cardigan", "jeans", "trousers", "skirt", "shorts", "dress",
    "trainers", "boots", "loafers", "bag", "scarf", "cap", "belt",
)


def _queries() -> list[str]:
    result = list(PRODUCT_QUERIES)
    for gender in ("menswear", "womenswear"):
        result.extend(f"{gender} {query}" for query in PRODUCT_QUERIES)
    result.extend(("vintage menswear", "vintage womenswear", "second hand vintage"))
    return result


def _search(client: httpx.Client, query: str) -> list[dict]:
    response = client.get(
        f"{PRODUCTION}/api/search",
        params={"q": query, "limit": 20},
    )
    response.raise_for_status()
    result = response.json()
    return result.get("items", []) if isinstance(result, dict) else result


def _production_image_url(value: str | None) -> str | None:
    if not value:
        return None
    if value.startswith("/"):
        return f"{PRODUCTION}{value}"
    return value


def _local_item(item: dict, query: str) -> dict | None:
    image_url = _production_image_url(item.get("image") or item.get("image_url"))
    if not item.get("id") or not item.get("source_url") or not image_url:
        return None
    return {
        "id": item["id"],
        "source": item.get("source") or "vinted",
        "source_url": item["source_url"],
        "title": item.get("title") or "",
        "brand": item.get("brand"),
        "price_gbp": item.get("price_gbp") or 0,
        "size": item.get("size"),
        "description": item.get("description"),
        "image_url": image_url,
        "image_path": None,
        "gender_hint": item.get("gender_hint"),
        "condition_hint": item.get("condition") or item.get("condition_hint"),
        "availability": item.get("availability"),
        "query": f"seed:{query}",
        "fetched_at": item.get("fetched_at") or datetime.now(UTC).isoformat(),
        "raw_json": item,
    }


def _cache_image(url: str, source_url: str) -> bool:
    from app import images

    path = images.image_path(url)
    if path.is_file():
        return True
    try:
        content = images._fetch_image(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return True
    except Exception as error:
        print(f"image skipped ({type(error).__name__}): {source_url}")
        return False


def main() -> None:
    if not os.getenv("DB_PATH"):
        raise SystemExit("Set DB_PATH to a local SQLite file, for example /tmp/q.sqlite.")
    if os.getenv("QUENYA_DATABASE_URL") or os.getenv("DATABASE_URL"):
        raise SystemExit("Remote database URLs are not allowed by this local seeder.")
    if any(os.getenv(name) for name in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")):
        raise SystemExit("Unset Supabase credentials before running this local seeder.")

    from app import db, images

    images.IMAGE_DIR = Path("/tmp/quenya-img")
    queries = _queries()
    records: dict[str, tuple[dict, dict]] = {}
    with httpx.Client(timeout=35, follow_redirects=True) as client:
        for query in queries:
            try:
                for item in _search(client, query):
                    row = _local_item(item, query)
                    if row:
                        records.setdefault(item["id"], (row, item))
            except httpx.HTTPError as error:
                print(f"search skipped ({type(error).__name__}): {query}")

    rows = [record[0] for record in records.values()]
    if not rows:
        raise SystemExit("No tagged catalogue items were returned.")
    inserted = db.insert_items(rows)
    tagged = 0
    for row, item in records.values():
        tags = item.get("tags")
        if isinstance(tags, dict):
            db.save_tags(
                row["id"],
                tags,
                item.get("tags_request_id") or "local-catalogue-seed",
                str(images.image_path(row["image_url"])),
            )
            tagged += 1

    image_urls = {
        row["image_url"]: row["source_url"]
        for row, _item in records.values()
    }
    cached = 0
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [
            executor.submit(_cache_image, url, source_url)
            for url, source_url in image_urls.items()
        ]
        for future in as_completed(futures):
            cached += int(future.result())

    db.invalidate_live_catalogue()
    total, live_tagged = db.total_counts()
    print(
        f"Read-only searches: {len(queries)}; catalogue items: {len(rows)} "
        f"({inserted} newly inserted); tagged: {tagged}; images cached: {cached}; "
        f"database total/tagged: {total}/{live_tagged}"
    )


if __name__ == "__main__":
    main()
