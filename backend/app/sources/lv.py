import hashlib
import json
import math
import random
import re
import time
from html.parser import HTMLParser
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit
from xml.etree import ElementTree

from app import db
from app.images import normalize_image_url

SITEMAP_URL = (
    "https://uk.louisvuitton.com/content/louisvuitton/sitemap/"
    "eng_GB/sitemap-product.xml"
)
_HEADERS = {"Accept-Language": "en-GB,en;q=0.9"}
_BLOCKED_BODY_MAX_BYTES = 5_000
_CLOTHING_KEYWORDS = (
    "shirt",
    "t-shirt",
    "tee",
    "polo",
    "trousers",
    "pants",
    "jeans",
    "denim",
    "shorts",
    "jacket",
    "blouson",
    "coat",
    "parka",
    "gilet",
    "vest",
    "blazer",
    "overshirt",
    "hoodie",
    "sweatshirt",
    "jumper",
    "sweater",
    "cardigan",
    "knit",
    "pullover",
    "dress",
    "skirt",
    "top",
    "tracksuit",
    "jogging",
)
_CLOTHING_RE = re.compile("|".join(re.escape(word) for word in _CLOTHING_KEYWORDS))
_EXCLUSION_RE = re.compile(
    r"(?<![a-z0-9])(?:"
    r"bags?|shoes?|sneakers?|trainers?|boots?|loafers?|sandals?|mules?|"
    r"slippers?|wallets?|belts?|scarves|scarfs?|hats?|caps?|beanies?|gloves?|"
    r"perfumes?|fragrances?|watches?|rings?|necklaces?|bracelets?|earrings?|"
    r"sunglasses|cases?|keychains?|charms?|pouches?|trunks?|luggage|"
    r"home(?:ware)?|dogs?|bab(?:y|ies)"
    r")(?![a-z0-9])"
)


class _JsonLdParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.scripts: list[str] = []
        self._current: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "script":
            return
        attributes = dict(attrs)
        script_type = (attributes.get("type") or "").split(";", 1)[0].strip().lower()
        if script_type == "application/ld+json":
            self._current = []

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._current.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._current is not None:
            self.scripts.append("".join(self._current))
            self._current = None


def _canonical_url(url: str) -> str | None:
    parts = urlsplit(url.strip())
    if (
        parts.scheme.lower() not in {"http", "https"}
        or (parts.hostname or "").lower() != "uk.louisvuitton.com"
        or not unquote(parts.path).lower().startswith("/eng-gb/products/")
    ):
        return None
    return urlunsplit(
        ("https", "uk.louisvuitton.com", parts.path.rstrip("/"), "", "")
    )


def _jsonld_objects(html: str) -> list[dict[str, Any]]:
    parser = _JsonLdParser()
    parser.feed(html or "")
    parser.close()
    objects = []
    for script in parser.scripts:
        try:
            parsed = json.loads(script)
        except (json.JSONDecodeError, TypeError):
            continue
        entries = parsed if isinstance(parsed, list) else [parsed]
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            objects.append(entry)
            graph = entry.get("@graph")
            if isinstance(graph, list):
                objects.extend(value for value in graph if isinstance(value, dict))
    return objects


def _has_type(value: dict[str, Any], expected: str) -> bool:
    item_type = value.get("@type")
    if isinstance(item_type, list):
        return expected in item_type
    return item_type == expected


def _gender_hint(
    breadcrumbs: list[dict[str, Any]], url: str
) -> str | None:
    names = []
    for breadcrumb in breadcrumbs:
        entries = breadcrumb.get("itemListElement")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if not name and isinstance(entry.get("item"), dict):
                name = entry["item"].get("name")
            if name:
                names.append(str(name).lower())

    for name in names:
        if re.search(r"\b(?:women(?:['’]s)?|womenswear|woman)\b", name):
            return "w"
        if re.search(r"\b(?:men(?:['’]s)?|menswear|man)\b", name):
            return "m"

    path = unquote(urlsplit(url).path).lower()
    if re.search(r"(?:^|[/_-])(?:women(?:['’]?s)?|womenswear|woman)(?=$|[/_-])", path):
        return "w"
    if re.search(r"(?:^|[/_-])(?:men(?:['’]?s)?|menswear|man)(?=$|[/_-])", path):
        return "m"
    return None


def _product_record(
    product: dict[str, Any] | None,
    breadcrumbs: list[dict[str, Any]],
    url: str,
) -> dict[str, Any] | None:
    canonical_url = _canonical_url(url)
    if product is None or canonical_url is None:
        return None

    offers = product.get("offers")
    if isinstance(offers, list):
        offers = offers[0] if offers else None
    if not isinstance(offers, dict) or str(
        offers.get("priceCurrency") or ""
    ).upper() != "GBP":
        return None
    try:
        price_gbp = float(str(offers.get("price") or "").replace(",", ""))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(price_gbp) or price_gbp <= 0:
        return None

    title = product.get("name")
    image = product.get("image")
    if isinstance(image, list):
        image = image[0] if image else None
    if isinstance(image, dict):
        image = image.get("url")
    if not title or not isinstance(image, str) or not image.strip():
        return None

    path_parts = [part for part in urlsplit(canonical_url).path.split("/") if part]
    sku = path_parts[-1] if len(path_parts) >= 4 else None
    item_id = f"lv:{sku}" if sku else f"lv:{hashlib.sha1(canonical_url.encode()).hexdigest()}"
    return {
        "id": item_id,
        "source": "louisvuitton",
        "source_url": canonical_url,
        "title": str(title),
        "brand": "Louis Vuitton",
        "price_gbp": price_gbp,
        "size": None,
        "description": product.get("description"),
        "image_url": normalize_image_url(image.strip()),
        "gender_hint": _gender_hint(breadcrumbs, canonical_url),
        "condition_hint": "new",
        "availability": offers.get("availability"),
        "query": "lv-sitemap",
        "raw_json": product,
    }


def _product_details(
    html: str, url: str
) -> tuple[dict[str, Any] | None, bool]:
    objects = _jsonld_objects(html)
    product = next((value for value in objects if _has_type(value, "Product")), None)
    breadcrumbs = [
        value for value in objects if _has_type(value, "BreadcrumbList")
    ]
    return _product_record(product, breadcrumbs, url), product is not None


def clothing_urls(xml_text: str) -> list[str]:
    root = ElementTree.fromstring(xml_text)
    result = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1].lower() != "loc" or not element.text:
            continue
        canonical_url = _canonical_url(element.text.strip())
        if canonical_url is None:
            continue
        path_parts = [
            part for part in unquote(urlsplit(canonical_url).path).split("/") if part
        ]
        try:
            products_index = path_parts.index("products")
            slug = path_parts[products_index + 1].lower()
        except (ValueError, IndexError):
            continue
        if _CLOTHING_RE.search(slug) and not _EXCLUSION_RE.search(slug):
            result.append(canonical_url)
    return list(dict.fromkeys(result))


def parse_product(html: str, url: str) -> dict[str, Any] | None:
    record, _has_product = _product_details(html, url)
    return record


def _response_text(response: Any) -> str:
    text = getattr(response, "text", "")
    if isinstance(text, str):
        return text
    return str(text)


def _response_size(response: Any, text: str) -> int:
    content = getattr(response, "content", None)
    return len(content) if isinstance(content, bytes) else len(text.encode("utf-8"))


def trickle(
    limit: int,
    *,
    session: Any = None,
    sleep=time.sleep,
    rng=random,
    existing_urls: set[str] | None = None,
    max_consecutive_blocks: int = 3,
    delay_range: tuple[float, float] = (20, 60),
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    report: dict[str, Any] = {
        "limit": limit,
        "clothing_urls": 0,
        "skipped_existing": 0,
        "available": 0,
        "attempted": 0,
        "ok": 0,
        "blocked": 0,
        "parse_failed": 0,
        "consecutive_blocks": 0,
        "stopped_after_blocks": False,
        "error": None,
    }
    owns_session = session is None
    if owns_session:
        try:
            from curl_cffi import requests

            session = requests.Session()
        except Exception as exc:
            report["error"] = f"session_error:{type(exc).__name__}"
            return [], report

    def get(url: str):
        return session.get(
            url,
            impersonate="safari15_5",
            timeout=25,
            headers=_HEADERS,
        )

    records: list[dict[str, Any]] = []
    try:
        try:
            sitemap_response = get(SITEMAP_URL)
        except Exception as exc:
            report["error"] = f"sitemap_request_error:{type(exc).__name__}"
            return records, report
        report["sitemap_status"] = getattr(sitemap_response, "status_code", None)
        if report["sitemap_status"] != 200:
            report["error"] = f"sitemap_http_{report['sitemap_status']}"
            return records, report
        try:
            filtered_urls = clothing_urls(_response_text(sitemap_response))
        except Exception as exc:
            report["error"] = f"sitemap_parse_error:{type(exc).__name__}"
            return records, report

        report["clothing_urls"] = len(filtered_urls)
        known_urls = set(existing_urls) if existing_urls is not None else db.source_urls()
        candidates = [url for url in filtered_urls if url not in known_urls]
        report["skipped_existing"] = len(filtered_urls) - len(candidates)
        report["available"] = len(candidates)
        rng.shuffle(candidates)
        consecutive_blocks = 0
        block_limit = max(1, max_consecutive_blocks)
        for index, url in enumerate(candidates[:max(0, limit)]):
            if index:
                sleep(rng.uniform(*delay_range))
            report["attempted"] += 1
            try:
                response = get(url)
            except Exception:
                response = None

            status = getattr(response, "status_code", None)
            text = _response_text(response) if response is not None else ""
            record, has_product = (
                _product_details(text, url) if status == 200 else (None, False)
            )
            blocked = status != 200 or (
                _response_size(response, text) <= _BLOCKED_BODY_MAX_BYTES
                and not has_product
            )
            if blocked:
                report["blocked"] += 1
                consecutive_blocks += 1
                report["consecutive_blocks"] = consecutive_blocks
                if consecutive_blocks >= block_limit:
                    report["stopped_after_blocks"] = True
                    break
                continue

            consecutive_blocks = 0
            report["consecutive_blocks"] = 0
            if record is None:
                report["parse_failed"] += 1
                continue
            records.append(record)
            report["ok"] += 1
    finally:
        if owns_session and session is not None:
            session.close()
    return records, report
