import json
import logging
import re
from collections import defaultdict, deque
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from app.config import CONTEXT_SOURCES, SPORT_IMAGE_UA, TAVILY_API_KEY

logger = logging.getLogger(__name__)
_SCRAPLING_UNAVAILABLE_LOGGED = False


def _scrapling_fetcher():
    global _SCRAPLING_UNAVAILABLE_LOGGED
    try:
        from scrapling.fetchers import Fetcher
    except ImportError:
        if not _SCRAPLING_UNAVAILABLE_LOGGED:
            logger.warning("Scrapling is unavailable; sport sources are disabled")
            _SCRAPLING_UNAVAILABLE_LOGGED = True
        return None
    return Fetcher

PRODUCT_DOMAINS = {
    "gymshark.com",
    "nike.com",
    "underarmour.co.uk",
    "lululemon.co.uk",
    "decathlon.co.uk",
    "sportsdirect.com",
    "cotswoldoutdoor.com",
    "arcteryx.com",
    "montane.com",
    "rab.equipment",
    "ellis-brigham.com",
    "patagonia.com",
}
_REPORT: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))


def _domain(url: str) -> str | None:
    host = (urlsplit(url).hostname or "").lower()
    if host.endswith("patagonia.com"):
        return "patagonia.com"
    return next(
        (domain for domain in PRODUCT_DOMAINS if host == domain or host.endswith(f".{domain}")),
        None,
    )


def _product_url(url: str, domain: str) -> bool:
    path = urlsplit(url).path.lower()
    checks = {
        "gymshark.com": r"/products?/",
        "nike.com": r"/gb/(?:t|a|w)/",
        "underarmour.co.uk": r"/(?:en-gb/)?p/",
        "lululemon.co.uk": r"/en-gb/p/",
        "decathlon.co.uk": r"/(?:p|product)/",
        "sportsdirect.com": r"/(?:product|p)/|-\d{5,}",
        "cotswoldoutdoor.com": r"/(?:products?|p)/",
        "arcteryx.com": r"/gb/(?:en/)?shop/|/product/",
        "montane.com": r"/products?/",
        "rab.equipment": r"/(?:uk/)?(?:products?|catalog/product)/",
        "ellis-brigham.com": r"/(?:products?|product)/",
        "patagonia.com": r"/(?:product|products)/",
    }
    if domain == "decathlon.co.uk" and "/vc/" in path:
        return False
    if domain == "nike.com" and "/gb/" not in path:
        return False
    if domain == "arcteryx.com" and not re.search(r"/(?:gb|uk)/", path):
        return False
    if domain == "rab.equipment" and not re.search(r"/uk/", path):
        return False
    if domain == "patagonia.com" and not re.search(r"/(?:eu|gb)/", path):
        return False
    return bool(re.search(checks[domain], path))


def _canonical(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return urlunsplit(("https", host, parts.path.rstrip("/"), "", ""))


class _AnchorParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            self.links.extend(
                value for name, value in attrs
                if name.lower() == "href" and value
            )


def _harvest_product_links(
    html: str,
    listing_url: str,
    domain: str,
    cached_urls: set[str],
    seen: set[str],
    *,
    limit: int = 6,
) -> tuple[list[str], list[dict[str, Any]]]:
    parser = _AnchorParser()
    parser.feed(html or "")
    harvested = []
    skipped = []
    for href in parser.links:
        absolute = urljoin(listing_url, href)
        parts = urlsplit(absolute)
        link_domain = _domain(absolute)
        canonical = _canonical(absolute)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            reason = "invalid_url"
        elif link_domain != domain:
            reason = "cross_domain_link"
        elif not _product_url(canonical, domain):
            reason = "not_product_url"
        elif canonical in cached_urls:
            reason = "cached_url"
        elif canonical in seen:
            reason = "duplicate_url"
        else:
            seen.add(canonical)
            harvested.append(canonical)
            if len(harvested) >= limit:
                break
            continue
        skipped.append({"url": canonical, "reason": reason})
    return harvested, skipped


def _type_is(value: Any, expected: str) -> bool:
    values = value if isinstance(value, list) else [value]
    return any(
        str(item).rsplit("/", 1)[-1].rsplit("#", 1)[-1].lower()
        == expected.lower()
        for item in values
    )


def _walk_jsonld(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_jsonld(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_jsonld(child)


def _image_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [url for item in value for url in _image_values(item)]
    if isinstance(value, dict):
        candidate = value.get("url") or value.get("contentUrl") or value.get("@id")
        return [candidate] if isinstance(candidate, str) else []
    return []


def parse_product_html(
    html: str,
    page_url: str,
    status: int = 200,
    *,
    gender: str | None = None,
    query: str = "",
) -> tuple[dict[str, Any] | None, str | None]:
    domain = _domain(page_url)
    if not domain or not _product_url(page_url, domain):
        return None, "not_product_url"
    if status != 200:
        return None, "http_404" if status == 404 else f"http_{status}"
    body = (html or "").lower()
    if any(
        text in body
        for text in (
            "access denied",
            "verify you are human",
            "captcha",
            "robot check",
            "unusual traffic",
            "security challenge",
        )
    ):
        return None, "challenge"
    products = []
    for script in re.findall(
        r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
        html or "",
        flags=re.I | re.S,
    ):
        try:
            parsed = json.loads(script.strip())
        except json.JSONDecodeError:
            continue
        products.extend(
            item for item in _walk_jsonld(parsed) if _type_is(item.get("@type"), "Product")
        )
    if not products:
        return None, "no_single_product"
    reason = "not_single_offer"
    for product in products:
        offer = product.get("offers")
        if isinstance(offer, list):
            if len(offer) != 1:
                reason = "multiple_offers"
                continue
            offer = offer[0]
        if not isinstance(offer, dict) or not _type_is(offer.get("@type"), "Offer"):
            reason = "not_single_offer"
            continue
        if str(offer.get("priceCurrency", "")).upper() != "GBP":
            reason = "not_gbp"
            continue
        price_match = re.search(
            r"£?\s*([\d,]+(?:\.\d{1,2})?)", str(offer.get("price") or "")
        )
        if not price_match:
            reason = "no_price"
            continue
        price = float(price_match.group(1).replace(",", ""))
        if price <= 0:
            reason = "no_price"
            continue
        title = str(product.get("name") or "").strip()
        if not title:
            reason = "no_title"
            continue
        image = next(
            (
                url
                for url in _image_values(product.get("image"))
                if url.startswith(("https://", "http://"))
            ),
            None,
        )
        if not image:
            reason = "no_product_image"
            continue
        brand_value = product.get("brand")
        if isinstance(brand_value, dict):
            brand_value = brand_value.get("name")
        brand = str(brand_value or title.split()[0])
        item_id = re.search(r"\d{5,}", urlsplit(page_url).path)
        item_key = (
            item_id.group(0)
            if item_id
            else re.sub(r"\W+", "-", title.lower()).strip("-")
        )
        return {
            "id": f"{domain}:{item_key}",
            "source": domain,
            "source_url": _canonical(page_url),
            "title": title,
            "brand": brand,
            "price_gbp": price,
            "size": None,
            "description": None,
            "image_url": image,
            "gender_hint": gender,
            "condition_hint": "new",
            "availability": offer.get("availability"),
            "query": query,
            "raw_json": {
                "provenance": "live",
                "price_source": "jsonld_product_offer",
                "price_currency": "GBP",
                "offer": offer,
            },
        }, None
    return None, reason


def parse_patagonia_extract(
    result: dict[str, Any],
    page_url: str,
    *,
    gender: str | None = None,
    query: str = "",
) -> tuple[dict[str, Any] | None, str | None]:
    if _domain(page_url) != "patagonia.com" or not _product_url(page_url, "patagonia.com"):
        return None, "not_product_url"
    content = result.get("raw_content") or ""
    headings = re.findall(r"(?m)^\s{0,3}#{1,3}\s+(.+?)\s*$", content)
    title = re.sub(r"[*_`]", "", headings[0]).strip() if headings else ""
    if not title:
        return None, "no_title"
    position = content.find(headings[0]) + len(headings[0])
    price_match = re.search(r"£\s*(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{2})?", content[position:])
    if not price_match:
        return None, "no_price"
    price_text = price_match.group(0)
    start = position + price_match.start()
    snippet = content[max(0, start - 80):start + len(price_text) + 80]
    shipping_pattern = r"(?:free\s+)?(?:shipping|delivery).{0,50}"
    if re.search(
        shipping_pattern + re.escape(price_text), snippet, re.I | re.S
    ):
        return None, "shipping_threshold"
    price = float(price_text.replace("£", "").replace(",", "").strip())
    product_id = re.search(r"/(?:product|products)/([^/?#]+)", urlsplit(page_url).path, re.I)
    product_token = product_id.group(1) if product_id else ""
    images = result.get("images") or []
    if isinstance(images, str):
        images = [images]
    image = None
    for item in images:
        candidate = item.get("url") if isinstance(item, dict) else item
        if (
            isinstance(candidate, str)
            and candidate.startswith(("http://", "https://"))
            and product_token
            and product_token.lower() in candidate.lower()
        ):
            image = candidate
            break
    if not image:
        return None, "no_product_image"
    item_key = product_token or re.sub(r"\W+", "-", title.lower()).strip("-")
    return {
        "id": f"patagonia.com:{item_key}",
        "source": "patagonia.com",
        "source_url": _canonical(page_url),
        "title": title,
        "brand": "Patagonia",
        "price_gbp": price,
        "size": None,
        "description": None,
        "image_url": image,
        "gender_hint": gender,
        "condition_hint": "new",
        "availability": None,
        "query": query,
        "raw_json": {
            "provenance": "live",
            "price_source": "tavily_extract_text",
            "price_snippet": snippet,
        },
    }, None


def _usable_image(url: str, page_url: str) -> bool:
    try:
        with httpx.stream(
            "GET",
            url,
            headers={"User-Agent": SPORT_IMAGE_UA, "Referer": page_url},
            timeout=12.0,
            follow_redirects=True,
        ) as response:
            return response.status_code == 200 and response.headers.get(
                "content-type", ""
            ).split(";", 1)[0].lower().startswith("image/")
    except httpx.HTTPError:
        return False


def _tavily_client():
    if not TAVILY_API_KEY:
        raise RuntimeError("TAVILY_API_KEY is not configured")
    from tavily import TavilyClient

    return TavilyClient(api_key=TAVILY_API_KEY)


def _round_robin(
    candidates: dict[str, deque[tuple[str, str, str, str, str | None]]],
    limit: int,
):
    selected = []
    domains = list(candidates)
    while domains and len(selected) < limit:
        for domain in list(domains):
            if len(selected) >= limit:
                break
            if candidates[domain]:
                selected.append((domain, *candidates[domain].popleft()))
            if not candidates[domain]:
                domains.remove(domain)
    return selected


def search_context(
    queries: list[str],
    context: str,
    gender: str,
    *,
    max_searches: int = 4,
    max_products: int = 12,
    cached_urls: set[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    Fetcher = _scrapling_fetcher()
    if Fetcher is None:
        return [], {}
    allowed = [
        domain for domain in CONTEXT_SOURCES.get(context, [])
        if domain in PRODUCT_DOMAINS
    ]
    if not allowed or not queries:
        return [], {}
    client = _tavily_client()
    candidates: dict[str, deque[tuple[str, str, str, str, str | None]]] = {
        domain: deque() for domain in allowed
    }
    seen: set[str] = set()
    seen_listing_pages: set[str] = set()
    listing_pages_by_domain: dict[str, int] = defaultdict(int)
    normalized_cached = {_canonical(url) for url in cached_urls or set()}
    job_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    candidate_diagnostics: list[dict[str, Any]] = []
    search_limit = min(max_searches, 4)
    product_limit = min(max_products, 12)

    def count(domain: str, key: str) -> None:
        job_counts[domain][key] += 1
        _REPORT[domain][key] += 1

    def note(
        url: str,
        domain: str | None,
        reason: str | None,
        source: str,
        query: str,
        *,
        status: str | None = None,
        listing_url: str | None = None,
    ) -> None:
        entry = {
            "url": url,
            "domain": domain,
            "source": source,
            "query": query,
            "status": status or ("dropped" if reason else "accepted"),
            "reason": reason,
        }
        if listing_url:
            entry["listing_url"] = listing_url
        candidate_diagnostics.append(entry)
        if len(candidate_diagnostics) > 30:
            del candidate_diagnostics[:-30]

    def queue_candidate(
        url: str,
        domain: str,
        title: str,
        query: str,
        source: str,
        listing_url: str | None = None,
    ) -> None:
        canonical = _canonical(url)
        if canonical in normalized_cached:
            count(domain, "cached_url")
            note(canonical, domain, "cached_url", source, query, listing_url=listing_url)
        elif canonical in seen:
            count(domain, "duplicate_url")
            note(
                canonical, domain, "duplicate_url", source, query,
                listing_url=listing_url,
            )
        else:
            seen.add(canonical)
            candidates[domain].append((canonical, title, query, source, listing_url))

    allowed_set = set(allowed)
    for query_index, query in enumerate(queries[:search_limit]):
        first = (query_index * 2) % len(allowed)
        search_domains = [allowed[first]]
        if len(allowed) > 1:
            search_domains.append(allowed[(first + 1) % len(allowed)])
        try:
            result = client.search(
                query=f"{query} UK",
                include_domains=search_domains,
                max_results=12,
                search_depth="basic",
                country="united kingdom",
            )
        except Exception:
            for domain in search_domains:
                count(domain, "search_error")
            continue
        for item in result.get("results", []):
            url = item.get("url") or ""
            domain = _domain(url)
            if not url:
                continue
            if domain not in allowed_set:
                note(url, domain, "not_allowed_domain", "search", query)
                continue
            if domain not in search_domains:
                count(domain, "outside_search_pair")
                note(url, domain, "outside_search_pair", "search", query)
                continue
            canonical = _canonical(url)
            if _product_url(canonical, domain):
                queue_candidate(
                    canonical, domain, item.get("title") or "", query, "search"
                )
                continue
            note(canonical, domain, "not_product_url", "search", query)
            count(domain, "not_product_url")
            if domain == "patagonia.com":
                continue
            if canonical in seen_listing_pages:
                count(domain, "duplicate_listing_page")
                note(
                    canonical, domain, "duplicate_listing_page", "listing",
                    query,
                )
                continue
            if listing_pages_by_domain[domain] >= 3:
                count(domain, "listing_page_cap")
                note(canonical, domain, "listing_page_cap", "listing", query)
                continue
            seen_listing_pages.add(canonical)
            listing_pages_by_domain[domain] += 1
            count(domain, "listing_pages")
            try:
                page = Fetcher.get(canonical, timeout=18)
                status = int(getattr(page, "status", 0) or 0)
                body = getattr(page, "body", b"")
                html = (
                    body.decode("utf-8", errors="replace")
                    if isinstance(body, bytes)
                    else str(body)
                )
            except Exception:
                count(domain, "listing_fetch_error")
                note(canonical, domain, "listing_fetch_error", "listing", query)
                continue
            if status != 200:
                reason = "http_404" if status == 404 else f"http_{status}"
                count(domain, reason)
                note(canonical, domain, reason, "listing", query)
                continue
            links, skipped_links = _harvest_product_links(
                html, canonical, domain, normalized_cached, seen
            )
            for skipped in skipped_links:
                count(domain, skipped["reason"])
                note(
                    skipped["url"], domain, skipped["reason"], "listing_link",
                    query, listing_url=canonical,
                )
            for link in links:
                count(domain, "harvested_links")
                candidates[domain].append(
                    (link, item.get("title") or "", query, "listing", canonical)
                )
    selected = _round_robin(candidates, product_limit)
    selected_urls = {(domain, candidate[0]) for domain, *candidate in selected}
    for domain, queued in candidates.items():
        for url, _title, query, source, listing_url in queued:
            if (domain, url) not in selected_urls:
                count(domain, "max_products")
                note(
                    url, domain, "max_products", source, query,
                    listing_url=listing_url,
                )
    records = []
    for domain, url, _search_title, query, source, listing_url in selected:
        count(domain, "product_fetches")
        if domain == "patagonia.com":
            try:
                response = client.extract(
                    urls=[url], extract_depth="advanced", include_images=True
                )
                found = next(
                    (item for item in response.get("results", []) if item.get("url") == url),
                    None,
                )
                record, reason = parse_patagonia_extract(
                    found or {}, url, gender=gender, query=query
                )
            except Exception:
                record, reason = None, "extract_error"
        else:
            try:
                page = Fetcher.get(url, timeout=18)
                body = getattr(page, "body", b"")
                html = (
                    body.decode("utf-8", errors="replace")
                    if isinstance(body, bytes)
                    else str(body)
                )
                record, reason = parse_product_html(
                    html,
                    str(getattr(page, "url", url)),
                    int(getattr(page, "status", 0) or 0),
                    gender=gender,
                    query=query,
                )
            except Exception:
                record, reason = None, "fetch_error"
        if record and not _usable_image(record["image_url"], record["source_url"]):
            record, reason = None, "image_unavailable"
        if record:
            records.append(record)
            count(domain, "accepted")
            note(
                url, domain, None, source, query, listing_url=listing_url
            )
        else:
            count(domain, reason or "invalid")
            note(
                url, domain, reason or "invalid", source, query,
                listing_url=listing_url,
            )
    job_report: dict[str, Any] = {
        domain: dict(counts) for domain, counts in job_counts.items()
    }
    job_report["candidate_diagnostics"] = candidate_diagnostics
    return records, job_report


def report() -> dict[str, dict[str, int]]:
    return {domain: dict(counts) for domain, counts in _REPORT.items()}
