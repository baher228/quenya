import re
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlsplit

from app.sources.lv import _jsonld_objects

from .common import (
    Blocked,
    ShopSession,
    eur_to_gbp,
    jsonld_products,
    make_row,
    request_succeeded,
    unique_rows,
)
from .jsonld import _image_url
from .shopify import gender_for
from .tees import tee_gender

_APPAREL_URL_PATTERN = (
    r"(?:t[-_ ]?shirts?|tees?|shirts?|polos?|sweat(?:shirts?)?|hoodies?|"
    r"jumpers?|sweaters?|knitwear|cardigans?|trousers?|pants?|jeans?|shorts?|"
    r"jackets?|coats?|parkas?|blazers?|overshirts?|dresses?|skirts?|tops?|tracksuits?)"
)
_OUT_OF_STOCK = re.compile(
    r"out\s*of\s*stock|outofstock|sold\s*out|discontinued|unavailable",
    re.IGNORECASE,
)


class _MetaParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta = {}

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "meta":
            return
        values = {key.lower(): value or "" for key, value in attrs if key}
        name = (
            values.get("property")
            or values.get("name")
            or values.get("itemprop")
            or ""
        ).strip().casefold()
        content = values.get("content", "").strip()
        if name and content:
            self.meta[name] = content


def _local_name(tag):
    return str(tag).rsplit("}", 1)[-1].casefold()


def _is_product(value):
    raw_types = value.get("@type") or []
    types = raw_types if isinstance(raw_types, list) else [raw_types]
    return any(
        str(item).rsplit("/", 1)[-1].rsplit("#", 1)[-1].casefold() == "product"
        for item in types
    )


def _matches_url(shop, url, pattern):
    include = str(shop.get("url_include") or "").casefold()
    if include and include not in url.casefold():
        return False
    path = unquote(urlsplit(url).path)
    return pattern.search(path) is not None


def _request_cap(exc):
    return "per-shop request cap reached" in str(exc)


def _sitemap_product_urls(shop, session, report):
    roots = shop.get("sitemap_url")
    if isinstance(roots, str):
        roots = [roots]
    if not isinstance(roots, list) or not roots:
        return []

    expression = re.compile(
        str(shop.get("url_pattern") or _APPAREL_URL_PATTERN), re.IGNORECASE
    )
    queue = [(str(url), 0) for url in roots if url]
    seen_sitemaps = set()
    product_urls = []
    seen_products = set()

    while queue:
        sitemap_url, depth = queue.pop(0)
        if sitemap_url in seen_sitemaps:
            continue
        seen_sitemaps.add(sitemap_url)
        try:
            fetched = session.get(sitemap_url)
        except RuntimeError as exc:
            if _request_cap(exc):
                report["request_cap_reached"] = True
                break
            raise
        if fetched is None:
            report["sitemaps_skipped_robots"] += 1
            continue
        body, _, _ = fetched
        report["sitemap_requests"] += 1
        if not request_succeeded(session):
            report["sitemaps_failed"] += 1
            continue
        try:
            root = ET.fromstring(body)
        except (ET.ParseError, TypeError, ValueError):
            report["sitemaps_failed"] += 1
            continue

        kind = _local_name(root.tag)
        locations = [
            (element.text or "").strip()
            for element in root.iter()
            if _local_name(element.tag) == "loc" and (element.text or "").strip()
        ]
        if kind == "sitemapindex":
            if depth < 2:
                queue.extend(
                    (urljoin(sitemap_url, location), depth + 1)
                    for location in locations
                )
            continue
        if kind != "urlset":
            continue
        for url in locations:
            if url not in seen_products and _matches_url(shop, url, expression):
                seen_products.add(url)
                product_urls.append(url)

    report["product_urls"] = len(product_urls)
    return product_urls


def _decimal_price(value):
    text = re.sub(r"\s+[A-Z]{3}\s*$", "", str(value).strip(), flags=re.IGNORECASE)
    try:
        price = Decimal(text.replace(",", ""))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return price if price.is_finite() and price > 0 else None


def _offer_nodes(value):
    if isinstance(value, list):
        for item in value:
            yield from _offer_nodes(item)
    elif isinstance(value, dict):
        if any(
            key in value
            for key in ("price", "lowPrice", "priceCurrency", "availability")
        ):
            yield value
        nested = value.get("offers")
        if nested is not None:
            yield from _offer_nodes(nested)


def _availability_is_out(value):
    return bool(_OUT_OF_STOCK.search(str(value or "")))


def _availability_name(value):
    return str(value or "InStock").rsplit("/", 1)[-1].rsplit("#", 1)[-1]


def _jsonld_prices(product):
    result = []
    for offer in _offer_nodes(product.get("offers") or []):
        amount = _decimal_price(
            offer.get("price") or offer.get("lowPrice")
        )
        if amount is None:
            continue
        result.append(
            {
                "price": amount,
                "currency": str(
                    offer.get("priceCurrency")
                    or product.get("priceCurrency")
                    or ""
                ).strip().upper(),
                "availability": str(
                    offer.get("availability")
                    or product.get("availability")
                    or ""
                ),
                "source": "jsonld:offers.price",
            }
        )
    return result


def _og_price(meta, product):
    amount = _decimal_price(
        meta.get("product:price:amount")
        or meta.get("og:price:amount")
        or meta.get("product:price.amount")
        or meta.get("og:price.amount")
    )
    if amount is None:
        return None
    return {
        "price": amount,
        "currency": str(
            meta.get("product:price:currency")
            or meta.get("og:price:currency")
            or meta.get("product:price.currency")
            or meta.get("og:price.currency")
            or ""
        ).strip().upper(),
        "availability": str(
            meta.get("product:availability")
            or meta.get("og:availability")
            or product.get("availability")
            or ""
        ),
        "source": "og:product:price",
    }


def _select_price(product, meta):
    nodes = list(_offer_nodes(product.get("offers") or []))
    offers = _jsonld_prices(product)
    available = [offer for offer in offers if not _availability_is_out(offer["availability"])]
    eligible = [
        offer for offer in available if offer["currency"] in {"GBP", "EUR"}
    ]
    if eligible:
        return min(eligible, key=lambda offer: offer["price"]), None

    if nodes and all(
        _availability_is_out(node.get("availability") or product.get("availability"))
        for node in nodes
    ):
        return None, "out_of_stock"
    if not nodes and _availability_is_out(product.get("availability")):
        return None, "out_of_stock"
    if offers and not available:
        return None, "out_of_stock"

    og_offer = _og_price(meta, product)
    if og_offer:
        if _availability_is_out(og_offer["availability"]):
            return None, "out_of_stock"
        if og_offer["currency"] in {"GBP", "EUR"}:
            return og_offer, None
        return None, "skipped_currency"

    if offers:
        return None, "skipped_currency"
    return None, "skipped_no_price"


def _product_brand(value):
    brands = value if isinstance(value, list) else [value]
    for brand in brands:
        if isinstance(brand, dict):
            brand = brand.get("name")
        if brand and str(brand).strip():
            return str(brand).strip()
    return ""


def _default_gender(shop, product_url):
    path = unquote(urlsplit(product_url).path).casefold()
    if re.search(r"(?:^|[/_-])women(?:s)?(?:[/_-]|$)", path):
        return "w"
    if re.search(r"(?:^|[/_-])men(?:s)?(?:[/_-]|$)", path):
        return "m"
    return str(shop.get("default_gender") or "m")


def _product_rows(product, meta, shop, page_url, report):
    selected, skipped = _select_price(product, meta)
    if skipped:
        report[skipped] += 1
        return []

    currency = selected["currency"]
    original_price = selected["price"]
    price = original_price
    extra = {
        "availability": _availability_name(selected["availability"]),
        "price_source": selected["source"],
    }
    if currency == "EUR":
        fx_rate = shop.get("_fx_rate")
        if fx_rate is None:
            report["skipped_currency"] += 1
            return []
        price = eur_to_gbp(original_price, fx_rate)
        extra.update(
            original_price=float(original_price),
            original_currency=currency,
            fx_rate=float(fx_rate),
        )

    title = str(product.get("name") or meta.get("og:title") or "").strip()
    image = _image_url(product.get("image") or "", shop) or meta.get("og:image", "")
    product_url = (
        product.get("url")
        or product.get("@id")
        or meta.get("og:url")
        or page_url
    )
    product_url = urljoin(page_url, str(product_url))
    product_type = str(
        product.get("category") or product.get("productType") or ""
    )
    product_for_gender = {
        "title": title,
        "product_type": product_type,
        "tags": product.get("tags") or [],
    }
    default_gender = _default_gender(shop, product_url)
    if shop.get("_category_set") == "tees":
        gender = tee_gender(shop, product_for_gender, product_url, default_gender)
    else:
        gender = gender_for(
            product_for_gender,
            shop.get("gender_rule", "default"),
            default_gender,
        )
    brand = _product_brand(product.get("brand")) or shop.get("brand")
    row_shop = dict(shop, brand=brand)
    row = make_row(
        row_shop,
        title,
        float(price),
        image,
        product_url,
        gender,
        page_url,
        {
            **extra,
            "product_type": product_type,
            "jsonld_brand": _product_brand(product.get("brand")) or None,
        },
    )
    if row:
        report["kept"] += 1
        return [row]
    report["skipped_unusable"] += 1
    return []


def scrape(shop: dict, session: ShopSession, probe: bool = False):
    report = {
        "checked": 0,
        "kept": 0,
        "skipped_no_price": 0,
        "skipped_currency": 0,
        "out_of_stock": 0,
        "request_failed": 0,
        "robots_skipped": 0,
        "skipped_unusable": 0,
        "sitemap_requests": 0,
        "sitemaps_failed": 0,
        "sitemaps_skipped_robots": 0,
        "product_urls": 0,
        "request_cap_reached": False,
    }
    shop["_sitemap_jsonld_report"] = report
    product_urls = _sitemap_product_urls(shop, session, report)
    if probe:
        product_urls = product_urls[:1]
    rows = []

    for product_url in product_urls:
        try:
            fetched = session.get(product_url)
        except RuntimeError as exc:
            if _request_cap(exc):
                report["request_cap_reached"] = True
                break
            raise
        except Blocked:
            report["checked"] += 1
            report["request_failed"] += 1
            raise
        report["checked"] += 1
        if fetched is None:
            report["robots_skipped"] += 1
            continue
        body, page_url, _ = fetched
        if not request_succeeded(session):
            report["request_failed"] += 1
            continue

        parser = _MetaParser()
        parser.feed(body or "")
        meta = parser.meta
        products = [
            value for value in _jsonld_objects(body or "") if _is_product(value)
        ]
        for value in jsonld_products(body or ""):
            if _is_product(value) and value not in products:
                products.append(value)
        if not products and _og_price(meta, {}):
            products = [{}]
        if not products:
            report["skipped_no_price"] += 1
            continue

        page_rows = []
        for product in products:
            page_rows.extend(_product_rows(product, meta, shop, page_url, report))
        rows.extend(page_rows)
        session.emit(page_rows)
        if getattr(session, "full", False):
            break

    return unique_rows(rows)
