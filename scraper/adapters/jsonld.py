import re
from urllib.parse import urljoin, urlsplit

from lxml import html as lxml_html

from .common import (
    ShopSession,
    accepts,
    jsonld_products,
    make_row,
    request_succeeded,
    unique_rows,
)


def _offers(product):
    offers = product.get("offers") or []
    return (
        [offers]
        if isinstance(offers, dict)
        else [item for item in offers if isinstance(item, dict)]
    )


def _price(product):
    candidates = []
    for offer in _offers(product):
        if str(offer.get("priceCurrency") or "").upper() != "GBP":
            continue
        availability = str(offer.get("availability") or "")
        if not re.search(r"InStock|LimitedAvailability", availability, re.I):
            continue
        try:
            price = float(offer.get("price") or offer.get("lowPrice") or 0)
        except (TypeError, ValueError):
            continue
        if price > 0:
            candidates.append((price, offer))
    return min(candidates, key=lambda item: item[0]) if candidates else (0, {})


def _valid_url(brand: str, url: str) -> bool:
    parts = urlsplit(url)
    path = parts.path
    host = (parts.hostname or "").lower()
    if host == "ssense.com" or host.endswith(".ssense.com"):
        return bool(re.match(r"^/en-gb/(?:men|women)/product/[^/]+/[^/]+/\d+$", path))
    if brand == "GANNI":
        return path.startswith("/en-gb/") and bool(re.search(r"-[A-Z]\d{4,}\.html$", path))
    if brand == "Whistles":
        return bool(re.match(r"^/product/[^/]+\.html$", path))
    return bool(path and path != "/")


def _title_from_url(brand: str, url: str) -> str:
    slug = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    if brand == "GANNI":
        slug = re.sub(r"\.html$", "", slug)
        slug = re.sub(r"-[A-Z]\d{4,}$", "", slug)
    elif brand == "Whistles":
        slug = re.sub(r"\.html$", "", slug)
        slug = re.sub(r"-\d+$", "", slug)
    title = slug.replace("-", " ").strip()
    title = re.sub(r"\bmens\b", "men's", title, flags=re.I)
    title = re.sub(r"\bwomens\b", "women's", title, flags=re.I)
    return title.title()


def _image_url(image, shop):
    if isinstance(image, list):
        image = image[0] if image else ""
    if isinstance(image, dict):
        image = image.get("url") or image.get("contentUrl") or ""
    if shop.get("key") == "ssense":
        image = str(image).replace("__IMAGE_PARAMS__", "b_white,c_lpad,g_center,h_960,w_960")
    return image


def _tile_rows(body: str, shop: dict, page_url: str, gender: str):
    try:
        root = lxml_html.fromstring(body)
    except (ValueError, TypeError):
        return []
    result = []
    for anchor in root.xpath("//a[@href]"):
        url = urljoin(page_url, str(anchor.get("href") or ""))
        if not _valid_url(str(shop.get("brand") or ""), url):
            continue
        node = anchor
        card_text, images = "", []
        for _ in range(9):
            if node is None:
                break
            card_text = " ".join(" ".join(node.itertext()).split())
            images = node.xpath(".//img[@src or @data-src or @data-lazy-src or @srcset]")
            if re.search(r"£\s*[\d,]+(?:\.\d{1,2})?", card_text) and images:
                break
            node = node.getparent()
        if not re.search(r"£\s*[\d,]+(?:\.\d{1,2})?", card_text):
            continue
        prices = [
            float(value.replace(",", ""))
            for value in re.findall(r"£\s*([\d,]+(?:\.\d{1,2})?)", card_text)
        ]
        if not prices or re.search(
            r"sold out|out of stock|coming soon|register interest", card_text, re.I
        ):
            continue
        title = " ".join(
            (anchor.get("title") or anchor.get("aria-label") or " ".join(anchor.itertext())).split()
        )
        if title.startswith("Open ") and title.endswith(" product page"):
            title = title[5:-13].strip()
        if title.lower() in {"clothing", "nike tech"} or title.lower().startswith(
            ("category for ", "select color:")
        ):
            title = ""
        title = title or _title_from_url(str(shop.get("brand") or ""), url)
        image = ""
        for item in images:
            candidate = item.get("src") or item.get("data-src") or item.get("data-lazy-src") or ""
            if not candidate and item.get("srcset"):
                candidate = item.get("srcset").split(",")[0].strip().split(" ")[0]
            if candidate and not candidate.lower().endswith(".svg"):
                image = urljoin(page_url, candidate)
                break
        if not title or not image or not accepts(
            shop, f"{title} {url}", title=title
        ):
            continue
        row = make_row(
            shop,
            title,
            min(prices),
            image,
            url,
            gender,
            page_url,
            {"availability": "InStock", "price_source": "listing_tile_gbp"},
        )
        if row:
            result.append(row)
    return result


def scrape(shop: dict, session: ShopSession, probe: bool = False):
    rows = []
    page_key = "accessory_pages" if shop.get("_category_set") == "accessories" else "pages"
    probe_page_count = (
        2
        if probe and shop.get("_category_set") == "accessories"
        else 1 if probe else None
    )
    for page in shop.get(page_key, [])[:probe_page_count]:
        gender, url = page["gender"], page["url"]
        fetched = session.get(url, browser=bool(shop.get("browser")))
        if fetched is None:
            shop["_status"] = "robots"
            shop["_reason"] = f"disallowed by robots.txt: {url}"
            continue
        body, final_url, _ = fetched
        if not request_succeeded(session):
            continue
        found = []
        for product in jsonld_products(body):
            price, offer = _price(product)
            image = _image_url(product.get("image") or "", shop)
            product_url = product.get("url") or offer.get("url") or product.get("@id") or ""
            title = str(product.get("name") or "").strip()
            availability = str(offer.get("availability") or "")
            if not _valid_url(str(shop.get("brand") or ""), urljoin(final_url, product_url)):
                continue
            if not title or title.lower() in {"clothing", "nike tech"}:
                title = _title_from_url(
                    str(shop.get("brand") or ""), urljoin(final_url, product_url)
                )
            brand = shop.get("brand")
            if shop.get("brand") is None:
                raw_brand = product.get("brand")
                brand = raw_brand.get("name") if isinstance(raw_brand, dict) else raw_brand
            row_shop = dict(shop, brand=brand)
            row = make_row(
                row_shop,
                title,
                price,
                image,
                urljoin(final_url, product_url),
                gender,
                final_url,
                {
                    "availability": availability.rsplit("/", 1)[-1],
                    "price_source": "jsonld:offers.price",
                },
            )
            if row:
                found.append(row)
        rows.extend(found)
        tile_found = _tile_rows(body, shop, final_url, gender)
        found.extend(tile_found)
        rows.extend(tile_found)
        session.emit(found)
        if probe:
            shop["_probe_rows"] = len(found)
    return unique_rows(rows)
