import re
from collections import defaultdict
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.config import TAVILY_API_KEY

RETAIL_DOMAINS = [
    "asos.com", "hm.com", "uniqlo.com", "cos.com", "arket.com", "zara.com",
    "endclothing.com", "ssense.com", "mrporter.com", "selfridges.com",
    "urbanoutfitters.com", "mango.com",
]
PRODUCT_PATHS = {
    "asos.com": re.compile(r"/prd/\d+(?:/|$)", re.I),
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
EXTRACT_CALL_LIMIT = 57
_extract_calls = 0
_report: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
_seen_urls: set[str] = set()
_NON_UK_LOCALES = {
    "us", "de", "fr", "it", "es", "au", "nl", "se", "dk", "no", "pl", "ru",
    "tr", "jp", "kr", "ca", "mx", "be", "ie", "fi", "pt", "ch", "at", "nz",
    "sg", "hk", "za", "ar", "br", "cz", "gr", "hu", "ro", "tw",
}


def _domain(url: str) -> str | None:
    host = (urlsplit(url).hostname or "").lower()
    return next(
        (domain for domain in RETAIL_DOMAINS if host == domain or host.endswith(f".{domain}")),
        None,
    )


def _candidate(url: str) -> bool:
    domain = _domain(url)
    return bool(domain and PRODUCT_PATHS[domain].search(urlsplit(url).path))


def _canonical(url: str, domain: str) -> str:
    parts = urlsplit(url)
    host = "www.asos.com" if domain == "asos.com" else (parts.hostname or "").lower()
    return urlunsplit(("https", host, parts.path.rstrip("/"), "", ""))


def _asos_locale_ok(url: str) -> bool:
    first = next((part for part in urlsplit(url).path.split("/") if part), "").lower()
    return first not in _NON_UK_LOCALES


def _product_id(url: str, domain: str) -> str | None:
    path = urlsplit(url).path
    if domain == "asos.com":
        match = re.search(r"/prd/(\d+)", path, re.I)
        return match.group(1) if match else None
    match = re.search(r"(\d{4,})", path)
    return match.group(1) if match else None


def _title_from_markdown(content: str) -> tuple[str | None, int]:
    match = re.search(r"(?m)^\s{0,3}#{1,3}\s+(.+?)\s*$", content)
    if not match:
        return None, -1
    title = re.sub(r"[*_`]", "", match.group(1)).strip()
    title = re.sub(r"\s+\|\s*ASOS\s*$", "", title, flags=re.I).strip()
    return (title or None), match.start(1)


def _fallback_title(title: str | None) -> str | None:
    if not title:
        return None
    cleaned = re.sub(r"\s+\|\s*ASOS\s*$", "", title, flags=re.I).strip()
    return cleaned or None


def _price_match(content: str, start: int) -> re.Match[str] | None:
    offset = max(0, start)
    now = re.compile(r"\bNow\s+£\d+(?:\.\d{2})?", re.I).search(content, offset)
    if now:
        return re.compile(r"£\d+(?:\.\d{2})?").search(content, now.start())
    return re.compile(r"£\d+(?:\.\d{2})?").search(content, offset)


def _image_urls(images: Any) -> list[str]:
    if not isinstance(images, list):
        return []
    urls = []
    for image in images:
        if isinstance(image, str):
            value = image
        elif isinstance(image, dict):
            value = image.get("url")
        else:
            value = None
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            urls.append(value)
    return urls


def _product_image_urls(
    images: Any, page_url: str, domain: str, product_id: str
) -> list[str]:
    canonical_page = _canonical(page_url, domain)
    urls = []
    for image_url in _image_urls(images):
        image_parts = urlsplit(image_url)
        image_domain = _domain(image_url)
        if image_domain == domain and (
            _candidate(image_url) or _canonical(image_url, domain) == canonical_page
        ):
            continue
        if domain == "asos.com":
            if (image_parts.hostname or "").lower() != "images.asos-media.com":
                continue
            if not re.search(
                rf"/products/[^/]+/{re.escape(product_id)}-", image_parts.path
            ):
                continue
        elif product_id not in image_url:
            continue
        urls.append(image_url)
    return urls


def parse_extract_result(
    result: dict[str, Any],
    *,
    page_url: str,
    search_title: str | None = None,
    gender: str | None = None,
    query: str = "",
) -> tuple[dict[str, Any] | None, str | None]:
    domain = _domain(page_url)
    if not domain or not _candidate(page_url):
        return None, "not_product_url"
    if domain == "asos.com" and not _asos_locale_ok(page_url):
        return None, "non_uk_locale"
    product_id = _product_id(page_url, domain)
    if not product_id:
        return None, "missing_product_id"
    content = result.get("raw_content") or result.get("content") or ""
    if not isinstance(content, str):
        content = ""
    title, title_start = _title_from_markdown(content)
    if not title:
        title = _fallback_title(search_title)
        title_start = 0
    if not title:
        return None, "no_title"
    match = _price_match(content, max(0, title_start))
    if not match:
        return None, "no_price"
    price = float(match.group(0).replace("£", ""))
    images = _product_image_urls(
        result.get("images"), page_url, domain, product_id
    )
    if not images:
        return None, "no_product_image"
    parts = urlsplit(page_url)
    canonical = _canonical(page_url, domain)
    if domain == "asos.com" and canonical != f"https://www.asos.com{parts.path.rstrip('/')}":
        return None, "non_uk_locale"
    snippet = content[max(0, match.start() - 60):min(len(content), match.end() + 60)]
    if domain == "asos.com":
        brand_slug = parts.path.strip("/").split("/")[0]
        brand = (
            brand_slug.replace("-", " ").title()
            if brand_slug.lower() != "prd"
            else (title.split()[0] if title.split() else None)
        )
    else:
        brand = title.split()[0] if title.split() else None
    item_id = product_id if domain == "asos.com" else product_id
    return {
        "id": f"{domain}:{item_id}",
        "source": "retail",
        "source_url": canonical,
        "title": title,
        "brand": brand,
        "price_gbp": price,
        "size": None,
        "description": None,
        "image_url": images[0],
        "gender_hint": gender,
        "condition_hint": "new",
        "availability": None,
        "query": query,
        "raw_json": {"price_source": "tavily_extract_text", "price_snippet": snippet},
    }, None


def get_domain_report() -> dict[str, dict[str, int]]:
    report = {
        domain: {"yield": _report[domain].get("yield", 0), **dict(_report[domain])}
        for domain in RETAIL_DOMAINS
    }
    report["extract_calls"] = {"total": _extract_calls}
    return report


def reset_domain_report() -> None:
    global _extract_calls
    _report.clear()
    _seen_urls.clear()
    _extract_calls = 0


def _tavily_client():
    if not TAVILY_API_KEY:
        raise RuntimeError("TAVILY_API_KEY is not configured")
    from tavily import TavilyClient

    return TavilyClient(api_key=TAVILY_API_KEY)


def _extract_batches(urls: list[str], client: Any) -> dict[str, dict[str, Any]]:
    global _extract_calls
    extracted: dict[str, dict[str, Any]] = {}
    for offset in range(0, len(urls), 10):
        if _extract_calls >= EXTRACT_CALL_LIMIT:
            for url in urls[offset:]:
                domain = _domain(url)
                if domain:
                    _report[domain]["extract_call_cap"] += 1
            break
        batch = urls[offset:offset + 10]
        _extract_calls += 1
        try:
            response = client.extract(
                urls=batch, extract_depth="advanced", include_images=True
            )
        except Exception:
            for url in batch:
                domain = _domain(url)
                if domain:
                    _report[domain]["extract_error"] += 1
            continue
        results = response.get("results") or []
        by_url = {item.get("url"): item for item in results if isinstance(item, dict)}
        for url in batch:
            if url in by_url:
                extracted[url] = by_url[url]
            else:
                domain = _domain(url)
                if domain:
                    _report[domain]["extract_error"] += 1
    return extracted


def search(
    query: str,
    per_page: int = 5,
    gender: str | None = None,
    *,
    client: Any = None,
) -> list[dict[str, Any]]:
    try:
        client = client or _tavily_client()
    except Exception:
        for domain in RETAIL_DOMAINS:
            _report[domain]["tavily_search_error"] += 1
        return []
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

    titles: dict[str, str] = {}
    candidates: list[str] = []
    for result in response.get("results") or []:
        url = result.get("url")
        if not isinstance(url, str):
            continue
        domain = _domain(url)
        if not domain:
            continue
        if domain == "asos.com" and not _asos_locale_ok(url):
            _report[domain]["non_uk_locale"] += 1
            continue
        if not _candidate(url):
            _report[domain]["not_product_url"] += 1
            continue
        canonical = _canonical(url, domain)
        if canonical in _seen_urls:
            _report[domain]["duplicate_url"] += 1
            continue
        if sum(_domain(candidate) == domain for candidate in candidates) >= per_page:
            continue
        candidates.append(url)
        titles[url] = str(result.get("title") or "")
    for image in response.get("images") or []:
        if isinstance(image, str):
            image_url = image
        elif isinstance(image, dict):
            image_url = image.get("url", "")
        else:
            image_url = ""
        match = re.search(r"images\.asos-media\.com/products/[^/]+/(\d+)-", image_url)
        if match:
            url = f"https://www.asos.com/prd/{match.group(1)}"
            if url not in candidates and url not in _seen_urls and sum(
                _domain(candidate) == "asos.com" for candidate in candidates
            ) < per_page:
                candidates.append(url)
                titles[url] = ""
    extracted = _extract_batches(candidates, client)
    records = []
    for url in candidates:
        domain = _domain(url)
        if not domain:
            continue
        _seen_urls.add(_canonical(url, domain))
        record, reason = parse_extract_result(
            extracted.get(url, {}),
            page_url=url,
            search_title=titles.get(url),
            gender=gender,
            query=query,
        )
        if not record:
            _report[domain][reason or "extract_error"] += 1
            continue
        records.append(record)
        _report[domain]["yield"] += 1
    return records
