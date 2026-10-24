import hashlib
import json
import re
import time
from collections import defaultdict
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from app.config import TAVILY_API_KEY

RETAIL_DOMAINS = [
    "asos.com",
    "hm.com",
    "uniqlo.com",
    "cos.com",
    "arket.com",
    "zara.com",
    "endclothing.com",
    "ssense.com",
    "mrporter.com",
    "selfridges.com",
    "urbanoutfitters.com",
    "mango.com",
]
PRODUCT_PATHS = {
    "asos.com": re.compile(r"/products/[^/?]+/\d+", re.I),
    "hm.com": re.compile(r"/(?:en_[a-z]{2}/)?productpage\.\d+\.html", re.I),
    "uniqlo.com": re.compile(r"/(?:uk/)?en/products/[^/?]+", re.I),
    "cos.com": re.compile(r"/(?:en_gbp/)?[^/?]+-\d{3,}", re.I),
    "arket.com": re.compile(r"/(?:en_gbp/)?[^/?]+-\d{3,}", re.I),
    "zara.com": re.compile(r"/(?:uk/)?en/[^/?]+-p\d+", re.I),
    "endclothing.com": re.compile(r"/product/[^/?]+", re.I),
    "ssense.com": re.compile(r"/en-gb/[^?#]+", re.I),
    "mrporter.com": re.compile(r"/en-gb/[^?#]+", re.I),
    "selfridges.com": re.compile(r"/(?:product/)?[^/?]+-\d{3,}", re.I),
    "urbanoutfitters.com": re.compile(r"/shop/[^/?]+", re.I),
    "mango.com": re.compile(r"/gb/en/p/[^/?]+", re.I),
}
DOMAIN_SECONDS_CAP = 18
PAGE_TIMEOUT_SECONDS = 12
_report: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))


class _ProductHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.jsonld: list[str] = []
        self.canonical: str | None = None
        self._in_jsonld = False
        self._script_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag.lower() == "link" and "canonical" in (attributes.get("rel") or "").lower():
            self.canonical = attributes.get("href") or self.canonical
        if tag.lower() == "script" and "ld+json" in (attributes.get("type") or "").lower():
            self._in_jsonld = True
            self._script_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_jsonld:
            self._script_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._in_jsonld:
            self.jsonld.append("".join(self._script_parts))
            self._script_parts = []
            self._in_jsonld = False


def _is_product(value: Any) -> bool:
    kind = value.get("@type") if isinstance(value, dict) else None
    if isinstance(kind, list):
        return any(str(entry).lower().rstrip("/").rsplit("/", 1)[-1] == "product" for entry in kind)
    return str(kind).lower().rstrip("/").rsplit("/", 1)[-1] == "product"


def _products(value: Any):
    if isinstance(value, list):
        for entry in value:
            yield from _products(entry)
    elif isinstance(value, dict):
        if _is_product(value):
            yield value
        for key in ("@graph", "mainEntity", "itemListElement"):
            if key in value:
                yield from _products(value[key])


def _first(value: Any) -> Any:
    return value[0] if isinstance(value, list) and value else value


def _plain(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("name") or value.get("url") or value.get("@id")
    return str(value).strip() if value is not None and str(value).strip() else None


def _product_from_node(
    node: dict[str, Any], canonical: str | None, page_url: str
) -> dict[str, Any] | None:
    offers = _first(node.get("offers"))
    if isinstance(offers, dict) and offers.get("@type") == "AggregateOffer":
        offers = _first(offers.get("offers")) or offers
    if not isinstance(offers, dict):
        return None
    currency = offers.get("priceCurrency") or node.get("priceCurrency")
    if str(currency).upper() != "GBP":
        return None
    price = offers.get("price") or offers.get("lowPrice")
    try:
        price_gbp = float(str(price).replace("£", "").replace(",", ""))
    except (TypeError, ValueError):
        return None
    image = _first(node.get("image"))
    if isinstance(image, dict):
        image = image.get("url") or image.get("contentUrl")
    name = _plain(node.get("name"))
    brand = _plain(node.get("brand"))
    image_url = urljoin(page_url, str(image)) if image else None
    url = urljoin(page_url, str(canonical or node.get("url") or page_url))
    parts = urlsplit(url)
    canonical_url = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    availability = offers.get("availability")
    if isinstance(availability, str):
        availability = availability.rstrip("/").split("/")[-1]
    if not name or not image_url or not canonical_url or price_gbp < 0:
        return None
    if urlsplit(image_url).scheme not in {"http", "https"}:
        return None
    return {
        "title": name,
        "brand": brand,
        "price_gbp": price_gbp,
        "image_url": image_url,
        "source_url": canonical_url,
        "availability": availability,
        "raw_json": node,
    }


def _parse_product_html(
    html: str, page_url: str
) -> tuple[dict[str, Any] | None, str | None]:
    parser = _ProductHTML()
    parser.feed(html)
    canonical = urljoin(page_url, parser.canonical) if parser.canonical else None
    saw_product = False
    last_reason = "missing_product_jsonld"
    for script in parser.jsonld:
        try:
            value = json.loads(script)
        except (json.JSONDecodeError, TypeError):
            continue
        for product in _products(value):
            saw_product = True
            record = _product_from_node(product, canonical, page_url)
            if record:
                return record, None
            offer = _first(product.get("offers"))
            if isinstance(offer, dict) and offer.get("@type") == "AggregateOffer":
                offer = _first(offer.get("offers")) or offer
            currency = offer.get("priceCurrency") if isinstance(offer, dict) else None
            if currency and str(currency).upper() != "GBP":
                last_reason = "non_gbp_price"
            elif not isinstance(offer, dict) or not (
                offer.get("price") or offer.get("lowPrice")
            ):
                last_reason = "missing_price"
            else:
                last_reason = "missing_required_product_fields"
    if saw_product:
        return None, last_reason
    return None, "missing_product_jsonld"


def parse_product_html(html: str, page_url: str) -> dict[str, Any] | None:
    """Extract a complete GBP Product from JSON-LD and canonical metadata."""
    return _parse_product_html(html, page_url)[0]


def _domain_for_url(url: str) -> str | None:
    host = (urlsplit(url).hostname or "").lower()
    return next(
        (
            domain
            for domain in RETAIL_DOMAINS
            if host == domain or host.endswith(f".{domain}")
        ),
        None,
    )


def _product_url(url: str) -> bool:
    domain = _domain_for_url(url)
    return bool(domain and PRODUCT_PATHS[domain].search(urlsplit(url).path))


def _response_html(response: Any) -> str:
    text = getattr(response, "text", None)
    if isinstance(text, str):
        return text
    body = getattr(response, "body", b"")
    if isinstance(body, bytes):
        return body.decode("utf-8", errors="replace")
    return str(body)


def _fetch_html(url: str, deadline: float) -> str | None:
    from scrapling.fetchers import Fetcher, StealthyFetcher

    plain_html = None
    try:
        timeout = min(PAGE_TIMEOUT_SECONDS, max(0.1, deadline - time.monotonic()))
        response = Fetcher().get(url, timeout=timeout, retries=0)
        status = getattr(response, "status", getattr(response, "status_code", 200))
        html = _response_html(response)
        if int(status) < 400 and html and parse_product_html(html, url):
            return html
        if int(status) < 400 and html:
            plain_html = html
    except Exception:
        pass
    if time.monotonic() >= deadline:
        return plain_html
    try:
        timeout = min(PAGE_TIMEOUT_SECONDS, max(0.1, deadline - time.monotonic()))
        response = StealthyFetcher.fetch(
            url, headless=True, timeout=timeout, retries=0
        )
        status = getattr(response, "status", getattr(response, "status_code", 200))
        html = _response_html(response)
        if int(status) < 400 and html and parse_product_html(html, url):
            return html
    except Exception:
        pass
    return plain_html


def _extract_from_tavily(
    url: str, client: Any, timeout: float
) -> dict[str, Any] | None:
    try:
        response = client.extract(urls=[url], timeout=max(0.1, timeout))
    except Exception:
        return None
    for result in response.get("results") or []:
        raw_content = result.get("raw_content") or result.get("content") or ""
        if isinstance(raw_content, str):
            record = parse_product_html(raw_content, url)
            if record:
                return record
            try:
                data = json.loads(raw_content)
            except (json.JSONDecodeError, TypeError):
                continue
            for product in _products(data):
                record = _product_from_node(product, None, url)
                if record:
                    return record
    return None


def _stable_id(domain: str, url: str) -> str:
    path = urlsplit(url).path.rstrip("/")
    sku = re.search(r"(\d{4,})(?:\.html)?$", path)
    identifier = sku.group(1) if sku else hashlib.sha1(url.encode()).hexdigest()
    return f"{domain}:{identifier}"


def _image_product_url(image_url: str) -> str | None:
    if "asos-media.com" not in image_url:
        return None
    match = re.search(r"/(\d+)-\d+(?:-[^/?]+)?(?:\.[a-z]+)?(?:[?#]|$)", image_url, re.I)
    return f"https://www.asos.com/prd/{match.group(1)}" if match else None


def get_domain_report() -> dict[str, dict[str, int]]:
    return {domain: dict(values) for domain, values in _report.items()}


def reset_domain_report() -> None:
    _report.clear()


def _tavily_client():
    if not TAVILY_API_KEY:
        raise RuntimeError("TAVILY_API_KEY is not configured")
    from tavily import TavilyClient

    return TavilyClient(api_key=TAVILY_API_KEY)


def search(
    query: str,
    per_page: int = 5,
    gender: str | None = None,
    *,
    client: Any = None,
) -> list[dict[str, Any]]:
    """Search retailer product pages and retain only complete GBP listings."""
    client = client or _tavily_client()
    try:
        response = client.search(
            query=f"{query} UK buy",
            include_domains=RETAIL_DOMAINS,
            include_images=True,
            country="united kingdom",
            max_results=max(10, per_page * 3),
        )
    except Exception:
        for domain in RETAIL_DOMAINS:
            _report[domain]["tavily_search_error"] += 1
        return []

    candidates: dict[str, list[str]] = defaultdict(list)
    for result in response.get("results") or []:
        url = result.get("url")
        if not isinstance(url, str):
            continue
        domain = _domain_for_url(url)
        if not domain:
            continue
        if not _product_url(url):
            _report[domain]["not_product_url"] += 1
            continue
        if url not in candidates[domain] and len(candidates[domain]) < per_page:
            candidates[domain].append(url)

    image_count = 0
    for image in response.get("images") or []:
        image_url = image if isinstance(image, str) else image.get("url", "")
        product_url = _image_product_url(image_url)
        if product_url and product_url not in candidates["asos.com"] and image_count < per_page:
            candidates["asos.com"].append(product_url)
            image_count += 1

    records: list[dict[str, Any]] = []
    for domain, urls in candidates.items():
        deadline = time.monotonic() + DOMAIN_SECONDS_CAP
        for url in urls:
            if time.monotonic() >= deadline:
                _report[domain]["domain_time_cap"] += 1
                break
            html = _fetch_html(url, deadline)
            record, reason = _parse_product_html(html, url) if html else (None, None)
            if not record:
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    record = _extract_from_tavily(url, client, remaining)
            if not record:
                if html:
                    _report[domain][reason or "missing_required_product_fields"] += 1
                else:
                    _report[domain]["fetch_failed"] += 1
                continue
            canonical_domain = _domain_for_url(record["source_url"])
            if canonical_domain != domain:
                _report[domain]["canonical_domain_mismatch"] += 1
                continue
            record.update(
                {
                    "id": _stable_id(domain, record["source_url"]),
                    "source": "retail",
                    "size": None,
                    "description": None,
                    "gender_hint": gender,
                    "condition_hint": "new",
                    "query": query,
                }
            )
            records.append(record)
            _report[domain]["yield"] += 1
    return records
