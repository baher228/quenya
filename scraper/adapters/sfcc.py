import re
from urllib.parse import parse_qs, urljoin, urlsplit

from lxml import html as lxml_html

from .common import ShopSession, jsonld_products, make_row, request_succeeded, unique_rows


def _text(nodes):
    return " ".join(" ".join(" ".join(node.itertext()).split()) for node in nodes)


def _price(text):
    values = []
    for amount in re.findall(r"£\s*([\d][\d,.]*)", text or ""):
        if amount.count(".") > 1:
            whole, fraction = amount.rsplit(".", 1)
            amount = whole.replace(",", "").replace(".", "") + "." + fraction
        else:
            amount = amount.replace(",", "")
        try:
            price = float(amount)
        except ValueError:
            continue
        if price > 0:
            values.append(price)
    return values


def _first_image(nodes, page_url):
    for node in nodes:
        for name in ("src", "data-src", "data-lazy-src", "data-original", "srcset", "data-srcset"):
            value = node.get(name)
            if not value:
                continue
            value = value.split(",")[0].strip().split()[0]
            candidate = urljoin(page_url, value)
            if candidate.startswith("https://"):
                return candidate
    return ""


def _product_rows(shop, body, page_url, gender):
    rows = []
    try:
        root = lxml_html.fromstring(body)
    except (ValueError, TypeError):
        root = None
    if shop["key"] == "moncler":
        for product in jsonld_products(body):
            offers = product.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            if offers.get("priceCurrency") != "GBP" or not re.search(
                r"InStock|LimitedAvailability", str(offers.get("availability") or ""), re.I
            ):
                continue
            image = product.get("image") or ""
            if isinstance(image, list):
                image = image[0] if image else ""
            row = make_row(
                shop,
                product.get("name", ""),
                float(offers.get("price") or 0),
                image,
                product.get("url") or product.get("@id") or "",
                gender,
                page_url,
                {
                    "availability": offers.get("availability", "InStock"),
                    "price_source": "moncler_jsonld_gbp",
                },
            )
            if row:
                rows.append(row)
    if root is None:
        return rows
    tile_xpath = {
        "stoneisland": "//*[contains(concat(' ', normalize-space(@class), ' '), ' product-tile ')]",
        "tnf": "//*[@data-test-id='product-card']",
        "lacoste": "//*[@data-pid]",
        "cpcompany": "//*[@data-pid]",
    }.get(shop["key"], "//*[@data-pid] | //*[@data-test-id='product-card']")
    products = jsonld_products(body) if shop["key"] == "stoneisland" else []
    for index, tile in enumerate(root.xpath(tile_xpath)):
        title_nodes = tile.xpath(
            ".//*[@data-test-id='product-card-title'] | "
            ".//*[contains(@class,'js-product-tile-title')] | "
            ".//*[contains(@class,'pdp-link')]//a | "
            ".//*[contains(concat(' ', normalize-space(@class), ' '), ' product-tile__seoname ')]"
        )
        title = _text(title_nodes) or tile.get("title", "")
        if shop["key"] == "stoneisland" and index < len(products):
            title = (
                _text(
                    tile.xpath(
                        ".//*[contains(concat(' ', normalize-space(@class), ' '), "
                        "' product-tile__seoname ')]"
                    )
                )
                or title
                or str(products[index].get("name") or "")
            )
        text = " ".join(tile.itertext())
        availability = (
            "OutOfStock"
            if re.search(r"sold out|out of stock|coming soon|register interest", text, re.I)
            else "InStock"
        )
        prices = _price(text)
        anchors = tile.xpath(".//a[@href]")
        url = anchors[0].get("href") if anchors else ""
        images = tile.xpath(".//img")
        image = _first_image(images, page_url)
        if shop["key"] == "stoneisland" and index < len(products):
            offer = products[index].get("offers") or {}
            if isinstance(offer, list):
                offer = offer[0] if offer else {}
            if not prices and str(offer.get("priceCurrency") or "").upper() == "GBP":
                try:
                    prices = [float(offer.get("price") or 0)]
                except (TypeError, ValueError):
                    prices = []
            url = url or products[index].get("url") or products[index].get("@id") or ""
            if not image:
                product_image = products[index].get("image") or ""
                image = (
                    product_image[0]
                    if isinstance(product_image, list) and product_image
                    else product_image
                )
        if not prices:
            continue
        if shop["key"] == "tnf":
            price_nodes = tile.xpath(".//*[@data-test-id='product-card-pricing']")
            prices = _price(_text(price_nodes))
        if shop["key"] == "lacoste":
            price_nodes = tile.xpath(".//*[contains(@class,'sales-price')]")
            prices = _price(_text(price_nodes)) or prices
        if not title or not image:
            continue
        row = make_row(
            shop,
            title,
            min(prices),
            image,
            urljoin(page_url, url),
            gender,
            page_url,
            {
                "availability": availability,
                "article": tile.get("data-pid"),
                "price_source": "sfcc_listing_gbp",
            },
        )
        if row:
            rows.append(row)
    return rows


def pagination_next(body, current_url):
    try:
        root = lxml_html.fromstring(body)
    except (ValueError, TypeError):
        return None
    current_path = urlsplit(current_url).path.rstrip("/")
    candidates = []
    for anchor in root.xpath("//a[@href]"):
        candidate = urljoin(current_url, anchor.get("href") or "")
        parsed = urlsplit(candidate)
        params = parse_qs(parsed.query)
        if parsed.path.rstrip("/") != current_path:
            continue
        label = " ".join(anchor.itertext()).strip().lower()
        rel = " ".join(anchor.get("rel") or []).lower()
        page_value = (params.get("page") or params.get("start") or params.get("offset") or [""])[0]
        numeric_label = label.isdigit() and label != "1"
        try:
            later_page = int(page_value) > (1 if "page" in params else 0)
        except ValueError:
            later_page = False
        explicit_next = "next" in rel or "next" in label or "view more" in label
        if explicit_next or numeric_label or later_page:
            candidates.append((0 if explicit_next else 1, candidate))
    return candidates[0][1] if candidates else None


def scrape(shop: dict, session: ShopSession, probe: bool = False):
    rows = []
    for page in shop.get("pages", [])[: 1 if probe else None]:
        gender, first_url = page["gender"], page["url"]
        current_url, visited = first_url, set()
        page_limit = 1 if probe else int(shop.get("max_pages", 4))
        for _ in range(page_limit):
            if current_url in visited:
                break
            visited.add(current_url)
            fetched = session.get(current_url, browser=shop.get("browser", False))
            if fetched is None:
                shop["_status"] = "robots"
                shop["_reason"] = f"disallowed by robots.txt: {current_url}"
                break
            body, final_url, _ = fetched
            if not request_succeeded(session):
                break
            found = _product_rows(shop, body, final_url, gender)
            rows.extend(found)
            session.emit(found)
            if probe:
                shop["_probe_rows"] = len(found)
                break
            next_url = pagination_next(body, final_url)
            if not next_url:
                break
            current_url = next_url
    return unique_rows(rows)
