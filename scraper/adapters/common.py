import hashlib
import json
import random
import re
import threading
import time
import urllib.robotparser
from urllib.parse import urljoin, urlsplit, urlunsplit

from scrapling.fetchers import Fetcher

from app.accessories import accessory_gender, accessory_kind

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
CHALLENGE_SIGNATURES = (
    "/cdn-cgi/challenge-platform",
    "cf-chl-",
    "captcha-delivery.com",
    "px-captcha",
    "_incapsula_resource",
    "pardon our interruption",
    "<title>just a moment",
    "<title>access denied",
)
WEAK_CHALLENGE_MARKERS = ("captcha", "verify you are human", "access denied")
EXPECTED_CONTENT_MARKERS = {
    "burberry": ("__preloaded_state__", "web-api/pages/products"),
    "jsonld": ("application/ld+json", "product-card", "product-tile"),
    "sfcc": ("product-tile", "data-pid", "product-card"),
    "tiles": ("product-tile", "data-pid", "product-card"),
    "nike": ("/gb/t/", "wall", "product-card"),
    "ssense": ("application/ld+json", "product-card", "product-tile"),
}
_robots_cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}
_robots_lock = threading.Lock()


class Blocked(Exception):
    def __init__(self, status: str, url: str, marker: str | None = None):
        reason = f"{status}: {url}"
        if marker:
            reason += f" (matched marker: {marker})"
        super().__init__(reason)
        self.status = status
        self.url = url
        self.marker = marker


def body_has_challenge(body: str, adapter: str = "") -> str | None:
    try:
        json.loads(body)
        return None
    except (ValueError, TypeError):
        pass
    value = body.lower()
    for marker in CHALLENGE_SIGNATURES:
        if marker in value:
            return marker
    if len(body.encode("utf-8", "replace")) < 15_000:
        expected = EXPECTED_CONTENT_MARKERS.get(adapter, ())
        if any(marker in value for marker in expected):
            return None
        for marker in WEAK_CHALLENGE_MARKERS:
            if marker in value:
                return marker
    return None


class ShopSession:
    def __init__(
        self,
        shop: dict,
        max_requests: int | None = None,
        delay: float | None = None,
        jitter: float | None = None,
        check_robots: bool = True,
        probe: bool = False,
    ):
        self.shop = shop
        self.max_requests = (
            max_requests if max_requests is not None else int(shop.get("max_requests", 60))
        )
        self.delay = float(shop.get("delay", 5.0) if delay is None else delay)
        self.jitter = float(shop.get("jitter", 1.5) if jitter is None else jitter)
        self.check_robots = check_robots and not probe
        self.count = 0
        self.last_request = 0.0
        self.errors = 0
        self.requests: list[dict] = []
        self.skipped: list[str] = []
        self.emitted: list[dict] = []
        self.on_emit = None
        self.deadline = None

    def emit(self, rows):
        rows = unique_rows(rows)
        self.emitted.extend(rows)
        if self.on_emit:
            self.on_emit(rows)

    def _pace(self):
        if self.last_request:
            time.sleep(
                max(
                    0,
                    self.delay
                    + random.uniform(0, self.jitter)
                    - (time.monotonic() - self.last_request),
                )
            )

    def _request(self, url: str, browser: bool = False):
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise TimeoutError("overall time budget reached")
        if self.count >= self.max_requests:
            raise RuntimeError("per-shop request cap reached")
        self._pace()
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise TimeoutError("overall time budget reached")
        if self.count >= self.max_requests:
            raise RuntimeError("per-shop request cap reached")
        self.count += 1
        self.last_request = time.monotonic()
        headers = {"User-Agent": UA, "Accept-Language": "en-GB,en;q=0.9"}
        try:
            if browser:
                from scrapling.fetchers import StealthyFetcher

                options = {
                    "headless": True,
                    "load_dom": True,
                    "network_idle": False,
                    "wait": 2500,
                    "timeout": 30000,
                    "retries": 1,
                    "retry_delay": 0,
                    "locale": "en-GB",
                    "timezone_id": "Europe/London",
                }
                if self.shop.get("key") == "ralphlauren":
                    options["wait_selector"] = "div.product-tile"
                response = StealthyFetcher.fetch(url, **options)
            else:
                response = Fetcher.get(
                    url,
                    headers=headers,
                    timeout=30,
                    follow_redirects=True,
                    retries=0,
                    retry_delay=0,
                )
        except Exception as exc:  # noqa: BLE001
            self.errors += 1
            self.requests.append(
                {"url": url, "final_url": url, "status": "error", "error": type(exc).__name__}
            )
            if self.errors >= 3:
                raise RuntimeError("three consecutive request errors") from exc
            return "", url, None
        status = int(getattr(response, "status", 0) or getattr(response, "status_code", 0) or 0)
        body = getattr(response, "body", b"")
        if not body:
            body = getattr(response, "text", "")
        text = body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body)
        final_url = str(getattr(response, "url", url) or url)
        self.requests.append({"url": url, "final_url": final_url, "status": status})
        if status in {403, 429, 503}:
            raise Blocked("blocked", url)
        marker = body_has_challenge(text, self.shop.get("adapter", ""))
        if marker:
            raise Blocked("challenge", url, marker)
        if status != 200:
            self.errors += 1
            if self.errors >= 3:
                raise RuntimeError("three consecutive request errors")
            return text, final_url, response
        self.errors = 0
        return text, final_url, response

    def _robots(self, url: str):
        host = urlsplit(url).netloc.lower()
        origin = f"{urlsplit(url).scheme}://{host}"
        with _robots_lock:
            if host in _robots_cache:
                return _robots_cache[host]
        try:
            body, _, _ = self._request(origin + "/robots.txt")
            if self.requests[-1]["status"] != 200:
                with _robots_lock:
                    _robots_cache[host] = None
                return None
            parser = urllib.robotparser.RobotFileParser()
            parser.set_url(origin + "/robots.txt")
            parser.parse(body.splitlines())
        except Blocked:
            raise
        except (ValueError, RuntimeError, TimeoutError):
            parser = None
        with _robots_lock:
            _robots_cache[host] = parser
        return parser

    def get(self, url: str, browser: bool = False):
        if self.check_robots:
            parser = self._robots(url)
            if parser and not (parser.can_fetch(UA, url) and parser.can_fetch("*", url)):
                self.skipped.append(url)
                return None
        return self._request(url, browser=browser)


def canonical_url(value: str, base: str, domain: str) -> str | None:
    url = urljoin(base, str(value or ""))
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    allowed = domain.lower().lstrip(".")
    if parts.scheme != "https" or not (host == allowed or host.endswith("." + allowed)):
        return None
    return urlunsplit(("https", parts.netloc.lower(), parts.path.rstrip("/"), "", ""))


def is_clothing(value: str) -> bool:
    return not bool(
        re.search(
            r"\b(accessor(?:y|ies)|hat|headwear|beanie|cap|bag|backpack|"
            r"bags?|backpacks?|scarf(?:s|ves)?|gloves?|shoes?|sneakers?|trainers?|"
            r"boots?|loafers?|sandals?|heels?|mules?|slippers?|footwear|"
            r"jewel(?:ry|lery)|earrings?|necklaces?|bracelets?|rings?|kids?|child|"
            r"underwear|socks?|gift\s*wrap(?:ping)?|gift\s*box(?:es)?|gift.?cards?|e.?gift|vouchers?|"
            r"membership|subscription|unlimited|donation|insurance|repair|service|"
            r"shipping|sample|bundle.?fee|protector|homeware|home goods|"
            r"skateboard|grips?|bolts?|"
            r"wallets?|purses?|belts?|watch(?:es)?|sunglasses|key.?ring|"
            r"infant|baby|toddler|kids?|kid['’]s|junior|girls?|boys?|youth|children|"
            r"age\s*\d{1,2}|\d{1,2}\s*yrs?|archive\s*[-–:]\s*vests?|"
            r"candle|mug|toy|rug|cushion|blanket|home decor)\b",
            value,
            re.I,
        )
    )


def accepts(shop: dict, text: str, title="", product_type="", tags=()) -> bool:
    if shop.get("_category_set", "apparel") == "accessories":
        return accessory_kind(title or text, product_type, tags) is not None
    return is_clothing(text)


def accessory_gender_for_shop(
    shop: dict, title: str, product_type: str = "", url: str = "", category: str = ""
) -> str:
    gender = accessory_gender(title, product_type, url, category)
    if gender != "unisex":
        return gender
    default = shop.get("accessory_default_gender")
    if default not in {"m", "w"}:
        default = {"men": "m", "women": "w"}.get(shop.get("gender_rule"))
    return {"m": "menswear", "w": "womenswear"}.get(default, gender)


def make_row(
    shop: dict, title: str, price: float, image: str, url: str, gender: str, page: str, extra=None
):
    from app.sources.listing_readers import infer_category

    extra = dict(extra or {})
    domain = shop["domain"]
    clean_url = canonical_url(url, page, domain)
    image = urljoin(page, str(image or ""))
    availability = str((extra or {}).get("availability") or "InStock")
    product_type = str(extra.get("product_type") or "")
    shopify_product = extra.get("shopify_product") or {}
    tags = shopify_product.get("tags") or extra.get("tags") or ()
    kind = (
        accessory_kind(title, product_type, tags)
        if shop.get("_category_set", "apparel") == "accessories"
        else None
    )
    try:
        price = float(price)
    except (TypeError, ValueError):
        return None
    if not (
        title
        and price > 0
        and image.startswith("https://")
        and not re.search(r"__|[{}]", image)
        and clean_url
        and accepts(
            shop,
            f"{title} {page}",
            title=title,
            product_type=product_type,
            tags=tags,
        )
        and not re.search(r"OutOfStock|SoldOut|Discontinued|Unavailable", availability, re.I)
    ):
        return None
    row_gender = gender
    category = None
    if kind:
        slot, category = kind
        label = accessory_gender_for_shop(shop, title, product_type, clean_url, category)
        row_gender = {"menswear": "m", "womenswear": "w"}.get(label)
    record = {
        "id": f"{domain}:{hashlib.sha1(clean_url.encode()).hexdigest()[:18]}",
        "source": domain,
        "source_url": clean_url,
        "title": title.strip(),
        "brand": shop.get("brand"),
        "price_gbp": price,
        "size": None,
        "description": None,
        "image_url": image,
        "gender_hint": row_gender if row_gender in {"m", "w"} else None,
        "condition_hint": "new",
        "availability": extra.pop("availability", "InStock"),
        "query": f"listing:{domain}",
        "raw_json": {
            "provenance": "live",
            "currency": "GBP",
            "category_page": page,
            **(
                {
                    "category_set": "accessories",
                    "accessory_slot": slot,
                    "accessory_category": category,
                }
                if kind
                else {}
            ),
            **extra,
        },
    }
    return {
        "url": clean_url,
        "source": domain,
        "title": title.strip(),
        "brand": shop.get("brand"),
        "price_gbp": price,
        "gender": (
            row_gender
            if kind
            else gender if gender in {"m", "w"} else "m"
        ),
        "category": category if kind else infer_category(title, page),
        "condition": "new",
        "image_url_original": image,
        "record": record,
    }


def unique_rows(rows):
    result = {}
    for row in rows:
        if row:
            result.setdefault(row["url"], row)
    return list(result.values())


def request_succeeded(session) -> bool:
    requests = getattr(session, "requests", [])
    return not requests or requests[-1].get("status", 200) == 200


def jsonld_products(html: str):
    from html.parser import HTMLParser

    class Scripts(HTMLParser):
        def __init__(self):
            super().__init__()
            self.capture = False
            self.parts = []
            self.scripts = []

        def handle_starttag(self, tag, attrs):
            if tag == "script" and "ld+json" in dict(attrs).get("type", "").lower():
                self.capture, self.parts = True, []

        def handle_data(self, data):
            if self.capture:
                self.parts.append(data)

        def handle_endtag(self, tag):
            if tag == "script" and self.capture:
                self.scripts.append("".join(self.parts))
                self.capture = False

    parser = Scripts()
    parser.feed(html)
    found = []

    def walk(value):
        if isinstance(value, dict):
            types = value.get("@type", [])
            if isinstance(types, str):
                types = [types]
            if "Product" in types:
                found.append(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    for script in parser.scripts:
        try:
            walk(json.loads(script))
        except (ValueError, TypeError):
            continue
    return found
