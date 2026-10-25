import json
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from .common import ShopSession, make_row, request_succeeded, unique_rows
from .jsonld import scrape as jsonld_scrape


def _product_url(url: str) -> bool:
    parts = urlsplit(url)
    return (
        parts.scheme == "https"
        and parts.hostname in {"nike.com", "www.nike.com"}
        and bool(re.match(r"^/gb/t/[^/]+/[^/]+$", parts.path))
    )


class _Tiles(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.cards = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.stack.append((tag, attrs))
        if tag == "a" and re.match(r"/gb/t/[^/]+/[^/]+", attrs.get("href", "")):
            self.current = {"url": attrs["href"], "title": attrs.get("aria-label", ""), "image": ""}
        if self.current and tag == "img" and not self.current["image"]:
            self.current["image"] = attrs.get("src") or attrs.get("data-src") or ""

    def handle_data(self, data):
        if self.current:
            self.current["text"] = self.current.get("text", "") + " " + data

    def handle_endtag(self, tag):
        if self.stack and self.stack[-1][0] == tag:
            self.stack.pop()
        if tag == "a" and self.current:
            self.cards.append(self.current)
            self.current = None


def scrape(shop: dict, session: ShopSession, probe: bool = False):
    rows = []
    for page in shop.get("pages", [])[: 1 if probe else None]:
        gender, url = page["gender"], page["url"]
        fetched = session.get(url, browser=bool(shop.get("browser")))
        if fetched is None:
            shop["_status"] = "robots"
            shop["_reason"] = f"disallowed by robots.txt: {url}"
            continue
        body, final_url, _ = fetched
        if not request_succeeded(session):
            continue
        page_rows = []
        # JSON-LD is preferred; tile parsing covers Nike's embedded listing cards.
        for row in jsonld_scrape(dict(shop, pages=[page]), _SingleSession(body, final_url), probe):
            if _product_url(row["url"]):
                page_rows.append(row)
        parser = _Tiles()
        parser.feed(body)
        for card in parser.cards:
            if re.search(r"sold out|out of stock|coming soon", card.get("text", ""), re.I):
                continue
            price_match = re.search(r"£\s*([\d,]+(?:\.\d{1,2})?)", card.get("text", ""))
            image = card.get("image", "")
            title = card.get("title") or ""
            if not title or not price_match or not image:
                continue
            title = re.sub(r"^Open (.+) product page$", r"\1", title).strip()
            price = float(price_match.group(1).replace(",", ""))
            product_url = urljoin(final_url, card["url"])
            if not _product_url(product_url):
                continue
            row = make_row(
                shop,
                title,
                price,
                image,
                product_url,
                gender,
                final_url,
                {"availability": "InStock", "price_source": "nike_listing_tile_gbp"},
            )
            if row:
                page_rows.append(row)
        # Inspect embedded Next data when product objects are not in HTML tiles.
        for match in re.finditer(
            r"<script[^>]+id=['\"]__NEXT_DATA__['\"][^>]*>(.*?)</script>", body, re.S
        ):
            try:
                data = json.loads(match.group(1))
            except ValueError:
                continue
            for product in _walk_products(data):
                availability = str(product.get("availability") or "")
                if product.get("available") is False or product.get("inStock") is False:
                    continue
                if availability and not re.search(
                    r"InStock|LimitedAvailability", availability, re.I
                ):
                    continue
                title = product.get("title") or product.get("productTitle") or product.get("name")
                price = product.get("currentPrice") or product.get("price")
                image = product.get("imageUrl") or product.get("image")
                href = product.get("pdpUrl") or product.get("url")
                if isinstance(price, dict):
                    price = price.get("value") or price.get("amount")
                try:
                    price = float(price)
                except (TypeError, ValueError):
                    continue
                product_url = urljoin(final_url, str(href or ""))
                if not _product_url(product_url):
                    continue
                row = make_row(
                    shop,
                    str(title or ""),
                    price,
                    str(image or ""),
                    product_url,
                    gender,
                    final_url,
                    {
                        "availability": availability.rsplit("/", 1)[-1] or "InStock",
                        "price_source": "nike_next_data_gbp",
                    },
                )
                if row:
                    page_rows.append(row)
        if probe:
            shop["_probe_rows"] = len(page_rows)
        rows.extend(page_rows)
        session.emit(page_rows)
    return unique_rows(rows)


class _SingleSession:
    def __init__(self, body, url):
        self.body, self.url = body, url

    def get(self, _url, browser=False):
        return self.body, self.url, None

    def emit(self, _rows):
        return None


def _walk_products(value):
    if isinstance(value, dict):
        if any(key in value for key in ("pdpUrl", "productTitle")):
            yield value
        for child in value.values():
            yield from _walk_products(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_products(child)
