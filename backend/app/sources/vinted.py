import hashlib
import os
import re
from dataclasses import asdict, is_dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from vinted_scraper import VintedScraper


def _get(value: Any, *names: str) -> Any:
    if isinstance(value, dict):
        for name in names:
            if value.get(name) is not None:
                return value[name]
    else:
        for name in names:
            found = getattr(value, name, None)
            if found is not None:
                return found
    return None


def _raw_item(item: Any) -> dict[str, Any]:
    if is_dataclass(item):
        return asdict(item)
    if isinstance(item, dict):
        return item
    return vars(item)


def _canonical_url(item: Any) -> str | None:
    path = _get(item, "url", "path")
    if not path:
        return None
    url = urljoin("https://www.vinted.co.uk", str(path))
    parts = urlsplit(url)
    if parts.netloc.lower() not in {"www.vinted.co.uk", "vinted.co.uk"}:
        return None
    return urlunsplit(("https", "www.vinted.co.uk", parts.path.rstrip("/"), "", ""))


def _first_photo(item: Any) -> str | None:
    photos = _get(item, "photos")
    if photos:
        photo = photos[0]
        photo_url = _get(photo, "url", "full_size_url", "fullsize_url", "url_full")
        if isinstance(photo_url, dict):
            photo_url = _get(photo_url, "url", "full_size_url")
        if photo_url:
            return str(photo_url)
    image = _get(item, "image", "img", "photo")
    if isinstance(image, dict):
        image = _get(image, "url", "full_size_url", "fullsize_url")
    return str(image) if image else None


def normalize_item(item: Any, query: str, gender: str) -> dict[str, Any] | None:
    url = _canonical_url(item)
    title = _get(item, "title")
    image_url = _first_photo(item)
    price = _get(item, "price")
    currency = _get(item, "currency", "currency_code")
    if not url or not title or not image_url or price is None:
        return None
    if currency and str(currency).upper() not in {"GBP", "£"}:
        return None
    try:
        price_gbp = float(price)
    except (TypeError, ValueError):
        return None
    if price_gbp < 0:
        return None
    brand = _get(item, "brand_title")
    if not brand:
        brand_value = _get(item, "brand")
        brand = _get(brand_value, "title", "name") or brand_value
    item_id = _get(item, "id")
    if not item_id:
        match = re.search(r"/items/(\d+)", url)
        item_id = match.group(1) if match else hashlib.sha1(url.encode("utf-8")).hexdigest()
    raw = _raw_item(item)
    return {
        "id": f"vinted:{item_id}",
        "source": "vinted",
        "source_url": url,
        "title": str(title),
        "brand": str(brand) if brand else None,
        "price_gbp": price_gbp,
        "size": _get(item, "size_title", "size"),
        "description": _get(item, "description"),
        "image_url": image_url,
        "gender_hint": gender,
        "condition_hint": "second-hand",
        "availability": _get(item, "status"),
        "query": query,
        "raw_json": raw,
    }


def search_direct(query: str, gender: str, per_page: int = 10) -> list[dict[str, Any]]:
    scraper = VintedScraper("https://www.vinted.co.uk")
    items = scraper.search({"search_text": query, "per_page": per_page})
    return [
        normalized
        for item in items
        if (normalized := normalize_item(item, query, gender)) is not None
    ]


def search(query: str, gender: str, per_page: int = 10) -> list[dict[str, Any]]:
    proxy_url = os.getenv("QUENYA_VINTED_PROXY_URL")
    if not proxy_url:
        return search_direct(query, gender, per_page)
    response = httpx.get(
        f"{proxy_url.rstrip('/')}/internal/vinted/search",
        params={"q": query, "gender": gender, "per_page": per_page},
        headers={
            "X-Quenya-Proxy-Key": os.getenv("QUENYA_VINTED_PROXY_KEY", "")
        },
        timeout=60,
    )
    if not response.is_success:
        raise RuntimeError(
            f"Vinted proxy HTTP {response.status_code}: {response.text[:200]}"
        )
    return response.json()["items"]
