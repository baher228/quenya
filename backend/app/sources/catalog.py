import hashlib
import io
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from PIL import Image

from app.config import SPORT_IMAGE_UA, TAVILY_API_KEY
from app.sources import is_unavailable, sport

logger = logging.getLogger(__name__)
_SCRAPLING_UNAVAILABLE_LOGGED = False


def _scrapling_fetchers():
    global _SCRAPLING_UNAVAILABLE_LOGGED
    try:
        from scrapling.fetchers import Fetcher, StealthyFetcher
    except ImportError:
        if not _SCRAPLING_UNAVAILABLE_LOGGED:
            logger.warning("Scrapling is unavailable; catalog sources are disabled")
            _SCRAPLING_UNAVAILABLE_LOGGED = True
        return None
    return Fetcher, StealthyFetcher

CATALOG: dict[str, dict[str, Any]] = {
    "ssense.com": {
        "method": "direct",
        "categories": {
            "m": [
                "https://www.ssense.com/en-gb/men/clothing/knitwear",
                "https://www.ssense.com/en-gb/men/clothing/jackets",
                "https://www.ssense.com/en-gb/men/clothing/shirts",
                "https://www.ssense.com/en-gb/men/clothing/trousers",
                "https://www.ssense.com/en-gb/men/clothing/denim",
            ],
            "w": [
                "https://www.ssense.com/en-gb/women/clothing/knitwear",
                "https://www.ssense.com/en-gb/women/clothing/coats",
                "https://www.ssense.com/en-gb/women/clothing/tops",
                "https://www.ssense.com/en-gb/women/clothing/trousers",
                "https://www.ssense.com/en-gb/women/clothing/dresses",
            ],
        },
    },
    "arket.com": {
        "method": "stealth_listing_tavily",
        "listing_preference": "stealth",
        "categories": {
            "m": [
                "https://www.arket.com/en-gb/men/clothing/knitwear/",
                "https://www.arket.com/en-gb/men/clothing/trousers/",
                "https://www.arket.com/en-gb/men/clothing/jackets-coats/",
            ],
            "w": [
                "https://www.arket.com/en-gb/women/clothing/knitwear/",
                "https://www.arket.com/en-gb/women/clothing/trousers/",
                "https://www.arket.com/en-gb/women/clothing/jackets-coats/",
            ],
        },
    },
    "mango.com": {
        "method": "stealth_listing_tavily",
        "listing_preference": "fetcher",
        "categories": {
            "m": [],
            "w": ["https://shop.mango.com/gb/en/c/women/trousers/0bf28b3b"],
        },
    },
    "uniqlo.com": {
        "method": "tavily",
        "categories": {
            "m": ["https://www.uniqlo.com/uk/en/men/jumpers"],
            "w": ["https://www.uniqlo.com/uk/en/women/bottoms/trousers-leggings-sweatpants"],
        },
    },
    "urbanoutfitters.com": {
        "method": "tavily",
        "categories": {
            "m": ["https://www.urbanoutfitters.com/en-gb/knitwear"],
            "w": ["https://www.urbanoutfitters.com/en-gb/womens-trousers"],
        },
    },
}

_PRODUCT_PATHS = {
    "ssense.com": re.compile(r"/en-gb/(?:men|women)/product/[^/?#]+", re.I),
    "cos.com": re.compile(r"/en-gb/.*/product/[^/?#]+", re.I),
    "arket.com": re.compile(r"/en-gb/product/[^/?#]+", re.I),
    "stories.com": re.compile(r"/en-gb/product/[^/?#]+", re.I),
    "barbour.com": re.compile(r"/gb/[^/?#]+\.html", re.I),
    "johnsmedley.com": re.compile(r"/uk/(?!mens(?:/|$)|womens(?:/|$))[^/?#]+", re.I),
    "charlestyrwhitt.com": re.compile(r"/uk/[^/?#]+/[^/?#]+\.html", re.I),
    "asos.com": re.compile(r"/[^/?#]+/[^/?#]+/prd/\d+", re.I),
    "mango.com": re.compile(r"/gb/en/p/[^?#]+", re.I),
    "burberry.com": re.compile(r"/[^/?#]+-p\d+", re.I),
    "ralphlauren.co.uk": re.compile(r"/en/(?!rlmag/|discover/)[^?#]+\.html", re.I),
    "hugoboss.com": re.compile(r"/uk/[^/?#]+/hbeu[a-z0-9_-]+\.html", re.I),
    "thenorthface.com": re.compile(
        r"/en-gb/p/[^/?#]+/[^/?#]+-nf0[a-z0-9]+/?$", re.I
    ),
    "stoneisland.com": re.compile(
        r"/en-gb/collection/[^/?#]+/[^/?#]+-l2s[a-z0-9]+\.html", re.I
    ),
    "uniqlo.com": re.compile(r"^/uk/en/products?/[^/?#]+(?:/\d{2})?/?$", re.I),
    "urbanoutfitters.com": re.compile(r"/(?:en-gb/)?shop/[^/?#]+", re.I),
}
_HOUSE_BRANDS = {
    "cos.com": "COS",
    "arket.com": "ARKET",
    "stories.com": "& Other Stories",
    "barbour.com": "Barbour",
    "johnsmedley.com": "John Smedley",
    "charlestyrwhitt.com": "Charles Tyrwhitt",
    "asos.com": "ASOS",
    "mango.com": "Mango",
    "burberry.com": "Burberry",
    "ralphlauren.co.uk": "Ralph Lauren",
}
_MAX_PER_DOMAIN = {domain: threading.BoundedSemaphore(2) for domain in CATALOG}
_TAVILY_DISABLED = False
_TAVILY_DISABLED_REASON: str | None = None
OLD_MONEY_TERMS = (
    "old money", "preppy", "quiet luxury", "ivy", "heritage",
    "country", "equestrian", "tailored",
)
OLD_MONEY_BRANDS = (
    "Ralph Lauren", "Barbour", "John Smedley", "Hackett", "Sunspel",
    "Charles Tyrwhitt", "Brooks Brothers", "Lacoste",
)
OLD_MONEY_SHOPS = (
    "barbour.com", "johnsmedley.com", "charlestyrwhitt.com",
    "burberry.com", "ralphlauren.co.uk",
)
OLD_MONEY_SLOT_WORDS = (
    "cable knit", "oxford shirt", "wax jacket", "chinos", "loafers",
)
_OLD_MONEY_INDEX = 0
_OLD_MONEY_LOCK = threading.Lock()


class _Anchors(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs: list[str] = []
        self.canonical: str | None = None

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag.lower() == "a" and values.get("href"):
            self.hrefs.append(values["href"])
        if tag.lower() == "link" and values.get("rel", "").lower() == "canonical":
            self.canonical = values.get("href")


def _root(url: str) -> str | None:
    host = (urlsplit(url).hostname or "").lower()
    return next(
        (
            domain for domain in _PRODUCT_PATHS
            if host == domain or host.endswith(f".{domain}")
        ),
        None,
    )


def _canonical(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(("https", (parts.hostname or "").lower(), parts.path.rstrip("/"), "", ""))


def _product_url(url: str, domain: str) -> bool:
    return bool(_PRODUCT_PATHS[domain].search(urlsplit(url).path))


def _type_is(value: Any, expected: str) -> bool:
    types = value.get("@type", []) if isinstance(value, dict) else []
    types = types if isinstance(types, list) else [types]
    return any(
        str(item).rsplit("/", 1)[-1].rsplit("#", 1)[-1].lower() == expected.lower()
        for item in types
    )


def _image_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [url for item in value for url in _image_values(item)]
    if isinstance(value, dict):
        url = value.get("url") or value.get("contentUrl") or value.get("@id")
        return [url] if isinstance(url, str) else []
    return []


def _image_candidates(url: str) -> list[str]:
    candidates = [url]
    if "__IMAGE_PARAMS__" in url:
        candidates.insert(0, url.replace(
            "__IMAGE_PARAMS__", "b_white,c_lpad,g_center,h_960,w_960"
        ))
    if re.search(r"(?i)(?:__SIZE__|__WIDTH__|{width})", url):
        candidates.insert(0, re.sub(
            r"(?i)(?:__SIZE__|__WIDTH__|{width})", "960", url
        ))
    if "ssensemedia.com" in url and "__IMAGE_PARAMS__" not in url:
        candidates.insert(0, url.replace(
            "/images/", "/images/b_white,c_lpad,g_center,h_960,w_960/", 1
        ))
    return list(dict.fromkeys(candidates))


def _valid_image(url: str, page_url: str) -> str | None:
    for candidate in _image_candidates(url):
        if not sport._usable_image(candidate, page_url):
            continue
        try:
            response = httpx.get(
                candidate,
                headers={"User-Agent": SPORT_IMAGE_UA, "Referer": page_url},
                follow_redirects=True,
                timeout=12,
            )
            content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            if response.status_code != 200 or not content_type.startswith("image/"):
                continue
            with Image.open(io.BytesIO(response.content)) as image:
                width, height = image.size
                image.verify()
            if min(width, height) >= 300:
                return candidate
        except Exception:
            continue
    return None


def _offers(offers: Any) -> list[dict[str, Any]]:
    values = offers if isinstance(offers, list) else [offers]
    normalized = []
    for value in values:
        if not isinstance(value, dict):
            continue
        keys = {str(key).lower(): key for key in value}
        offer = dict(value)
        for canonical in ("priceCurrency", "lowPrice", "highPrice"):
            key = keys.get(canonical.lower())
            if key is not None:
                offer[canonical] = value[key]
        normalized.append(offer)
    return normalized


def _gbp_price(offers: Any) -> float | None:
    for offer in _offers(offers):
        if str(offer.get("priceCurrency") or "").upper() != "GBP":
            continue
        raw_price = offer.get("lowPrice") if _type_is(offer, "AggregateOffer") else None
        raw_price = raw_price or offer.get("price")
        try:
            price = float(str(raw_price).replace(",", "").replace("£", "").strip())
        except (TypeError, ValueError):
            continue
        if price > 0:
            return price
    return None


def _record(
    item: dict[str, Any],
    group: dict[str, Any],
    page_url: str,
    domain: str,
    gender: str,
    query: str,
) -> dict[str, Any] | None:
    item_url = item.get("url") or group.get("url")
    if isinstance(item_url, str):
        candidate_url = urljoin(page_url, item_url)
        if _root(candidate_url) == domain and _product_url(candidate_url, domain):
            page_url = candidate_url
    if not _product_url(page_url, domain):
        return None
    offers = item.get("offers") or group.get("offers")
    normalized_offers = _offers(offers)
    available_offers = [
        offer
        for offer in normalized_offers
        if not is_unavailable(offer.get("availability"))
    ]
    if normalized_offers and not available_offers:
        return None
    price = _gbp_price(available_offers if normalized_offers else offers)
    title = str(item.get("name") or group.get("name") or "").strip()
    if not title or price is None:
        return None
    image_values = _image_values(item.get("image")) or _image_values(group.get("image"))
    image = next(
        (usable for value in image_values if (usable := _valid_image(value, page_url))),
        None,
    )
    if not image:
        return None
    brand = (
        item.get("brandName") or group.get("brandName")
        or item.get("brand") or group.get("brand")
    )
    if isinstance(brand, dict):
        brand = brand.get("name")
    canonical = _canonical(page_url)
    raw_id = re.sub(r"\W+", "-", canonical.lower()).strip("-")
    return {
        "id": f"{domain}:{hashlib.sha1(raw_id.encode()).hexdigest()[:18]}",
        "source": domain,
        "source_url": canonical,
        "title": title,
        "brand": str(brand or _HOUSE_BRANDS.get(domain) or title.split()[0]),
        "price_gbp": price,
        "size": None,
        "description": None,
        "image_url": image,
        "gender_hint": gender,
        "condition_hint": "new",
        "availability": next(
            (
                offer.get("availability")
                for offer in available_offers
                if offer.get("availability")
            ),
            None,
        ),
        "query": query,
        "raw_json": {
            "provenance": "live",
            "price_source": "jsonld_gbp_offer",
            "currency": "GBP",
            "product_structure": _structure(item, group),
        },
    }


def _structure(item: dict[str, Any], group: dict[str, Any]) -> str:
    offers = item.get("offers") or group.get("offers")
    if _type_is(group, "ProductGroup"):
        if any(_type_is(offer, "AggregateOffer") for offer in _offers(offers)):
            return "ProductGroup/hasVariant/AggregateOffer"
        if isinstance(offers, list):
            return "ProductGroup/hasVariant/offers[]"
        return "ProductGroup/hasVariant/Offer"
    if any(_type_is(offer, "AggregateOffer") for offer in _offers(offers)):
        return "Product/AggregateOffer"
    if isinstance(offers, list):
        return "Product/offers[]"
    return "Product/Offer"


def _json_values(html: str):
    scripts = re.findall(
        r"<script\b[^>]*>(.*?)</script\s*>", html or "", flags=re.I | re.S
    )
    for script in scripts:
        body = script.strip()
        if not body:
            continue
        candidates = [body]
        if re.match(r"window\.__(?:INITIAL_STATE__|NEXT_DATA__)", body):
            candidates = [body.split("=", 1)[-1].strip().rstrip(";")]
        for candidate in candidates:
            try:
                yield json.loads(candidate)
            except (json.JSONDecodeError, TypeError):
                continue


def _product_records(html: str, page_url: str, domain: str, gender: str, query: str):
    canonical_parser = _Anchors()
    canonical_parser.feed(html or "")
    canonical = canonical_parser.canonical
    if canonical and _root(urljoin(page_url, canonical)) == domain:
        page_url = _canonical(urljoin(page_url, canonical))
    seen: set[str] = set()
    for value in _json_values(html):
        for node in sport._walk_jsonld(value):
            if _type_is(node, "Product"):
                key = json.dumps(node, sort_keys=True, default=str)
                if key not in seen:
                    seen.add(key)
                    record = _record(node, node, page_url, domain, gender, query)
                    if record:
                        yield record
            elif _type_is(node, "ProductGroup"):
                variants = node.get("hasVariant") or []
                variants = variants if isinstance(variants, list) else [variants]
                for variant in variants[:1]:
                    if isinstance(variant, dict):
                        record = _record(variant, node, page_url, domain, gender, query)
                        if record:
                            yield record
        for node in _walk_embedded(value):
            if node.get("name") or node.get("title"):
                record = _record(node, node, page_url, domain, gender, query)
                if record:
                    yield record


def _walk_embedded(value: Any):
    if isinstance(value, dict):
        normalized = {str(k).lower(): v for k, v in value.items()}
        has_price = any(key in normalized for key in ("offers", "price", "priceamount"))
        has_name = any(key in normalized for key in ("name", "title", "productname"))
        if has_price and has_name:
            offer = normalized.get("offers")
            if not offer and normalized.get("price") is not None:
                offer = {
                    "price": normalized["price"],
                    "priceCurrency": normalized.get("pricecurrency"),
                }
            candidate = {
                "name": (
                    normalized.get("name")
                    or normalized.get("title")
                    or normalized.get("productname")
                ),
                "offers": offer,
                "image": (
                    normalized.get("image")
                    or normalized.get("images")
                    or normalized.get("imageurl")
                ),
                "brand": normalized.get("brand"),
            }
            yield candidate
        for child in value.values():
            yield from _walk_embedded(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_embedded(child)


def parse_product_html(
    html: str,
    page_url: str,
    *,
    gender: str = "u",
    query: str = "",
) -> list[dict[str, Any]]:
    domain = _root(page_url)
    if not domain:
        return []
    records = {}
    for record in _product_records(html, page_url, domain, gender, query):
        records.setdefault(record["source_url"], record)
    return list(records.values())


def _listing_urls(html: str, page_url: str, domain: str) -> list[str]:
    parser = _Anchors()
    parser.feed(html or "")
    links = []
    for href in parser.hrefs:
        url = urljoin(page_url, href)
        if _root(url) == domain and _product_url(url, domain):
            links.append(_canonical(url))
    pattern = _PRODUCT_PATHS[domain].pattern
    for match in re.findall(r"""["']([^"']+)["']""", html or ""):
        url = urljoin(page_url, match.replace("\\/", "/"))
        if _root(url) == domain and re.search(pattern, urlsplit(url).path):
            links.append(_canonical(url))
    return list(dict.fromkeys(links))


def _fetch(url: str, domain: str, *, stealth: bool = False, listing: bool = False):
    fetchers = _scrapling_fetchers()
    if fetchers is None:
        return "", url, None
    Fetcher, StealthyFetcher = fetchers
    semaphore = _MAX_PER_DOMAIN[domain]
    with semaphore:
        time.sleep(0.15)
        try:
            if stealth:
                page = StealthyFetcher.fetch(
                    url,
                    headless=True,
                    network_idle=True,
                    load_dom=True,
                    wait=800,
                    timeout=10000,
                    locale="en-GB",
                    timezone_id="Europe/London",
                )
            else:
                page = Fetcher.get(url, timeout=18)
            status = int(getattr(page, "status", 0) or 0)
            body = getattr(page, "body", b"")
            html = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body)
            final_url = str(getattr(page, "url", url) or url)
            return html if status == 200 and _root(final_url) == domain else "", final_url, status
        except Exception:
            return "", url, None


def _tavily_client():
    if not TAVILY_API_KEY:
        raise RuntimeError("TAVILY_API_KEY is not configured")
    from tavily import TavilyClient

    return TavilyClient(api_key=TAVILY_API_KEY)


def _quota_error(exc: Exception) -> bool:
    return bool(re.search(r"quota|credit|monthly limit|rate limit", str(exc), re.I))


def _tavily_search(client: Any, domain: str, query: str) -> list[tuple[str, str]]:
    global _TAVILY_DISABLED, _TAVILY_DISABLED_REASON
    if _TAVILY_DISABLED:
        return []
    try:
        response = client.search(
            query=f"{query} UK buy",
            include_domains=[domain],
            include_images=True,
            country="united kingdom",
            max_results=20,
        )
        return [
            (item["url"], str(item.get("title") or ""))
            for item in response.get("results", [])
            if isinstance(item.get("url"), str) and _root(item["url"]) == domain
        ]
    except Exception as exc:
        if _quota_error(exc):
            _TAVILY_DISABLED = True
            _TAVILY_DISABLED_REASON = f"{type(exc).__name__}: {exc}"
        return []


def _tavily_records(
    client: Any,
    domain: str,
    urls: list[tuple[str, str]],
    gender: str,
    query: str,
) -> list[dict[str, Any]]:
    global _TAVILY_DISABLED, _TAVILY_DISABLED_REASON
    if not urls or _TAVILY_DISABLED:
        return []
    by_url = {}
    for start in range(0, len(urls), 10):
        if _TAVILY_DISABLED:
            break
        batch = urls[start : start + 10]
        try:
            response = client.extract(
                urls=[url for url, _ in batch],
                extract_depth="advanced",
                include_images=True,
            )
            by_url.update({
                item.get("url"): item
                for item in response.get("results", [])
                if item.get("url")
            })
        except Exception as exc:
            if _quota_error(exc):
                _TAVILY_DISABLED = True
                _TAVILY_DISABLED_REASON = f"{type(exc).__name__}: {exc}"
            break
    records = []
    for url, search_title in urls:
        result = by_url.get(url)
        if not result:
            continue
        raw = result.get("raw_content") or result.get("content") or ""
        title = search_title or result.get("title") or ""
        heading = re.search(r"(?m)^\s{0,3}#{1,3}\s+(.+?)\s*$", raw)
        if heading:
            title = re.sub(r"[*_`]", "", heading.group(1)).strip()
        structured = parse_product_html(raw, url, gender=gender, query=query)
        if structured:
            records.extend(structured[:1])
            continue
        match = re.search(r"£\s*(\d[\d,]*(?:\.\d{1,2})?)", raw)
        if not title or not match:
            continue
        price = float(match.group(1).replace(",", ""))
        if price <= 0:
            continue
        images = result.get("images") or []
        if isinstance(images, str):
            images = [images]
        image_url = next((
            item.get("url") if isinstance(item, dict) else item
            for item in images
            if isinstance(item.get("url") if isinstance(item, dict) else item, str)
            and _valid_image(item.get("url") if isinstance(item, dict) else item, url)
        ), None)
        if not image_url:
            continue
        canonical = _canonical(url)
        records.append({
            "id": f"{domain}:{hashlib.sha1(canonical.encode()).hexdigest()[:18]}",
            "source": domain,
            "source_url": canonical,
            "title": title,
            "brand": _HOUSE_BRANDS.get(domain, str(title.split()[0])),
            "price_gbp": price,
            "size": None,
            "description": None,
            "image_url": image_url,
            "gender_hint": gender,
            "condition_hint": "new",
            "availability": None,
            "query": query,
            "raw_json": {"provenance": "tavily_extract", "price_currency": "GBP"},
        })
    return records


def _slots_for_url(url: str) -> set[str]:
    path = urlsplit(url).path.lower()
    if any(term in path for term in ("knit", "sweater", "jumper")):
        return {"top", "knitwear"}
    if any(term in path for term in ("trouser", "jean", "denim", "bottom")):
        return {"bottom"}
    if any(term in path for term in ("jacket", "coat", "outerwear")):
        return {"outerwear"}
    if "dress" in path:
        return {"dress"}
    if any(term in path for term in ("shirt", "top")):
        return {"top"}
    return set()


def category_urls(domain: str, gender_code: str, pieces: list[str] | None = None) -> list[str]:
    data = CATALOG.get(domain)
    if not data:
        return []
    urls = (
        data["categories"].get("m", []) + data["categories"].get("w", [])
        if gender_code == "u"
        else data["categories"].get(gender_code, [])
    )
    if not pieces:
        return list(urls)
    requested = set(pieces)
    return [
        url for url in urls
        if not _slots_for_url(url) or _slots_for_url(url) & requested
    ]


def crawl(
    domain: str,
    gender_code: str,
    per_category: int,
    cached_urls: set[str],
    tavily_budget: int,
    *,
    pieces: list[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    config = CATALOG.get(domain)
    if not config:
        return [], {"domain": domain, "error": "unsupported_domain", "fetched": 0}
    if _scrapling_fetchers() is None:
        return [], {
            "domain": domain,
            "error": "scrapling_unavailable",
            "fetched": 0,
        }
    if gender_code not in {"m", "w", "u"}:
        gender_code = "m" if gender_code == "menswear" else "w"
    categories = category_urls(domain, gender_code, pieces)
    report: dict[str, Any] = {
        "domain": domain, "gender": gender_code, "method": config["method"],
        "categories": [], "fetched": 0, "tavily_urls_used": 0,
    }
    if not categories or per_category <= 0:
        return [], report
    records: list[dict[str, Any]] = []
    seen = {_canonical(url) for url in cached_urls}
    client = None
    remaining = max(0, int(tavily_budget))

    if config["method"] == "tavily":
        try:
            client = _tavily_client()
        except Exception as exc:
            report["error"] = f"{type(exc).__name__}: {exc}"
            return [], report
        targets: list[tuple[str, str]] = []
        for category in categories:
            category_gender = "men" if "/men/" in category else "women"
            query = f"{category_gender} clothing {category.rsplit('/', 1)[-1]}"
            for url, title in _tavily_search(client, domain, query):
                if _product_url(url, domain) and _canonical(url) not in seen:
                    targets.append((url, title))
                    seen.add(_canonical(url))
                    if sum(_root(item[0]) == domain for item in targets) >= (
                        per_category * len(categories)
                    ):
                        break
        targets = targets[:remaining]
        report["tavily_urls_used"] = len(targets)
        report["categories"] = [
            {"url": url, "method": "tavily_search", "candidates": 0}
            for url in categories
        ]
        records.extend(_tavily_records(client, domain, targets, gender_code, f"catalog:{domain}"))
    else:
        target_urls: list[str] = []
        for category in categories:
            preference = config.get("listing_preference", "fetcher")
            order = [True, False] if preference == "stealth" else [False, True]
            html = ""
            method = "none"
            status = None
            final_url = category
            for stealth in order:
                html, final_url, status = _fetch(category, domain, stealth=stealth, listing=True)
                if html:
                    method = "StealthyFetcher" if stealth else "Fetcher"
                    links = _listing_urls(html, final_url, domain)
                    if links:
                        break
            links = _listing_urls(html, final_url, domain)
            category_records = []
            for record in _product_records(
                html, final_url, domain, gender_code, f"catalog:{domain}"
            ):
                category_records.append(record)
                if len(category_records) >= per_category:
                    break
            for record in category_records:
                url = record["source_url"]
                if url not in seen and len(records) < per_category * len(categories):
                    seen.add(url)
                    records.append(record)
            links = [url for url in links if url not in seen]
            target_urls.extend(links[:per_category])
            report["categories"].append({
                "url": category, "final_url": final_url, "status": status,
                "method": method, "candidates": len(links),
            })
        target_urls = list(dict.fromkeys(target_urls))
        target_urls = target_urls[: per_category * len(categories)]
        product_results: list[tuple[str, list[dict[str, Any]], str | None]] = []

        def parse_target(url: str):
            html, final_url, status = _fetch(url, domain)
            parsed = (
                parse_product_html(
                    html, final_url, gender=gender_code, query=f"catalog:{domain}"
                )
                if html and status == 200
                else []
            )
            return url, parsed, final_url

        with ThreadPoolExecutor(max_workers=2) as executor:
            product_results = list(executor.map(parse_target, target_urls))
        tavily_targets: list[tuple[str, str]] = []
        for url, parsed, _final_url in product_results:
            if parsed:
                for record in parsed:
                    if record["source_url"] not in seen:
                        seen.add(record["source_url"])
                        records.append(record)
            elif config["method"] == "stealth_listing_tavily":
                tavily_targets.append((url, ""))
        if tavily_targets:
            tavily_targets = tavily_targets[:remaining]
            if tavily_targets:
                try:
                    client = client or _tavily_client()
                except Exception as exc:
                    report["tavily_error"] = f"{type(exc).__name__}: {exc}"
                if client:
                    report["tavily_urls_used"] = len(tavily_targets)
                    records.extend(
                        _tavily_records(
                            client, domain, tavily_targets, gender_code,
                            f"catalog:{domain}",
                        )
                    )
    report["fetched"] = len(records)
    report["tavily_disabled"] = _TAVILY_DISABLED
    if _TAVILY_DISABLED_REASON:
        report["tavily_disabled_reason"] = _TAVILY_DISABLED_REASON
    return records, report


def old_money_queries(count: int, gender_code: str = "u") -> list[str]:
    global _OLD_MONEY_INDEX
    if count <= 0:
        return []
    with _OLD_MONEY_LOCK:
        start = _OLD_MONEY_INDEX
        _OLD_MONEY_INDEX = (_OLD_MONEY_INDEX + count) % len(OLD_MONEY_BRANDS)
    queries = []
    for offset in range(count):
        brand = OLD_MONEY_BRANDS[(start + offset) % len(OLD_MONEY_BRANDS)]
        slot = OLD_MONEY_SLOT_WORDS[(start + offset) % len(OLD_MONEY_SLOT_WORDS)]
        queries.append(f"{brand} {slot}")
    return queries


def is_old_money(brief: dict[str, Any], taste: dict[str, Any] | None = None) -> bool:
    taste = taste or {}
    values = [str(brief.get("text") or "")]
    aesthetics = taste.get("aesthetics") or []
    values.extend(str(value) for value in aesthetics)
    values.extend(str(value) for value in (taste.get("likes") or {}).keys())
    haystack = " ".join(values).casefold()
    return any(term in haystack for term in OLD_MONEY_TERMS)
