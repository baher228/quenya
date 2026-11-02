#!/usr/bin/env python3
"""Seed a LOCAL SQLite catalogue with real listings for the pitch-deck screenshots.

New pieces come from the public Shopify product feeds of shops that are enabled in
scraper/shops.toml. Second-hand pieces come from live Vinted UK search through the
backend's own Vinted reader. Nothing is written anywhere except DB_PATH.

Run from backend/:
    DB_PATH=... PYTHON_DOTENV_DISABLED=1 ../.venv/bin/python ../pitch-deck/tools/seed_catalogue.py
"""

from __future__ import annotations

import hashlib
import os
import random
import re
import sys
import time
import tomllib
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

if not os.getenv("DB_PATH") or os.getenv("QUENYA_DATABASE_URL"):
    raise SystemExit("Set DB_PATH to a local SQLite file and unset QUENYA_DATABASE_URL.")

from app import db  # noqa: E402
from app.sources import vinted  # noqa: E402

SHOPS = [
    "sunspel", "folk", "margarethowell", "drakes", "albam", "oliverspencer",
    "kestin", "privatewhite", "oipolloi", "studionicholson", "ymc", "percival",
    "communityclothing", "finisterre", "universalworks_tees",
]
PER_SHOP = 16
VINTED_QUERIES = [
    "cashmere jumper men", "merino crew neck jumper", "Margaret Howell shirt",
    "suede loafers men", "Paraboot", "wool overcoat men", "pleated trousers men",
    "chore jacket men", "Sunspel t-shirt", "oxford shirt men", "suede chelsea boots",
    "knitted polo men", "wool cardigan men", "selvedge jeans",
]
PER_QUERY = 8
WOMEN = re.compile(r"\b(women'?s?|womens|ladies|dress|skirt|bra|bikini)\b", re.I)
SKIP = re.compile(
    r"\b(gift|card|voucher|sock|socks|boxer|brief|trunks|candle|mug|book|poster|sample|"
    r"repair|bag|tote|wallet|keyring|umbrella|towel|swim)\b",
    re.I,
)


def shop_configs() -> dict[str, dict]:
    data = tomllib.loads((ROOT / "scraper" / "shops.toml").read_text())
    return {shop["key"]: shop for shop in data["shop"] if shop.get("enabled")}


def shopify_rows(client: httpx.Client, shop: dict) -> list[dict]:
    host = shop["host"]
    response = client.get(f"https://{host}/products.json", params={"limit": 250})
    response.raise_for_status()
    rows = []
    for product in response.json().get("products", []):
        title = product.get("title") or ""
        if WOMEN.search(title) or SKIP.search(title) or SKIP.search(product.get("product_type") or ""):
            continue
        tags = product.get("tags") or []
        tags = tags if isinstance(tags, list) else str(tags).split(",")
        if any("women" in str(tag).lower() or "female" in str(tag).lower() for tag in tags):
            continue
        variants = [variant for variant in product.get("variants", []) if variant.get("available")]
        images = product.get("images") or []
        if not variants or not images:
            continue
        price = float(variants[0]["price"])
        if price <= 0:
            continue
        url = f"https://{host}/products/{product['handle']}"
        rows.append({
            "id": f"{shop['domain']}:{hashlib.sha1(url.encode()).hexdigest()[:18]}",
            "source": shop["domain"].removeprefix("www."),
            "source_url": url,
            "title": title,
            "brand": product.get("vendor") or shop.get("brand"),
            "price_gbp": price,
            "size": None,
            "description": re.sub(r"<[^>]+>", " ", product.get("body_html") or "")[:600] or None,
            "image_url": images[0]["src"].split("?")[0] + "?width=1200",
            "gender_hint": "m",
            "condition_hint": "new",
            "availability": "in_stock",
            "query": f"deck-seed:{shop['key']}",
            "raw_json": {"provenance": "shopify_products_json", "product_type": product.get("product_type")},
        })
    random.Random(shop["key"]).shuffle(rows)
    return rows[:PER_SHOP]


def main() -> None:
    db.init_db()
    configs = shop_configs()
    rows: list[dict] = []
    with httpx.Client(
        timeout=30,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (Quenya local catalogue seed)"},
    ) as client:
        for key in SHOPS:
            shop = configs.get(key)
            if not shop:
                print(f"skip {key}: not enabled")
                continue
            try:
                found = shopify_rows(client, shop)
                rows.extend(found)
                print(f"{key}: {len(found)}")
            except Exception as error:
                print(f"{key}: failed ({type(error).__name__}: {error})")
            time.sleep(1.5)
    for query in VINTED_QUERIES:
        try:
            found = vinted.search_direct(query, "m", per_page=PER_QUERY)
            rows.extend(found)
            print(f"vinted '{query}': {len(found)}")
        except Exception as error:
            print(f"vinted '{query}': failed ({type(error).__name__}: {error})")
    inserted = db.insert_items(rows)
    db.invalidate_live_catalogue()
    print(f"rows: {len(rows)}, inserted: {inserted}, totals: {db.total_counts()}")


if __name__ == "__main__":
    main()
