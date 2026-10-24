import json
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

from app.sources import catalog

DOMAINS = {
    "cos.com", "arket.com", "stories.com", "barbour.com",
    "charlestyrwhitt.com", "johnsmedley.com", "asos.com", "mango.com",
    "burberry.com", "ralphlauren.co.uk",
}
PRODUCT_PATHS = {
    "cos.com": re.compile(r"/en-gb/.*/product/[^/?#]+", re.I),
    "arket.com": re.compile(r"/en-gb/product/[^/?#]+", re.I),
    "stories.com": re.compile(r"/en-gb/product/[^/?#]+", re.I),
    "barbour.com": re.compile(r"/gb/[^/?#]+\.html", re.I),
    "charlestyrwhitt.com": re.compile(r"/uk/[^/?#]+/[^/?#]+\.html", re.I),
    "johnsmedley.com": re.compile(r"/uk/(?!mens(?:/|$)|womens(?:/|$))[^/?#]+", re.I),
    "asos.com": re.compile(r"/[^/?#]+/[^/?#]+/prd/\d+", re.I),
    "mango.com": re.compile(r"/gb/en/p/[^?#]+", re.I),
    "burberry.com": re.compile(r"/[^/?#]+-p\d+", re.I),
    "ralphlauren.co.uk": re.compile(r"/en/(?!rlmag/|discover/)[^?#]+\.html", re.I),
}
PRICE = re.compile(r"£\s*([\d,]+(?:\.\d{1,2})?)")


class _Node:
    def __init__(self, tag: str, attrs: dict[str, str | None], parent=None):
        self.tag, self.attrs, self.parent = tag, attrs, parent
        self.children: list[_Node | str] = []

    def text(self) -> str:
        return " ".join(
            part if isinstance(part, str) else part.text() for part in self.children
        ).strip()

    def descendants(self, tag: str) -> list["_Node"]:
        result = []
        for child in self.children:
            if isinstance(child, _Node):
                if child.tag == tag:
                    result.append(child)
                result.extend(child.descendants(tag))
        return result


class _Tree(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("document", {})
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _Node(tag.lower(), dict(attrs), self.stack[-1])
        self.stack[-1].children.append(node)
        if tag.lower() not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(_Node(tag.lower(), dict(attrs), self.stack[-1]))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag.lower():
                del self.stack[index:]
                return

    def handle_data(self, data):
        if data.strip():
            self.stack[-1].children.append(data.strip())


def _domain(value: str) -> str:
    host = (urlsplit(value).hostname or value).lower()
    return next((d for d in DOMAINS if host == d or host.endswith(f".{d}")), "")


def _canonical(value: str, page_url: str, domain: str) -> str | None:
    absolute = urljoin(page_url, value)
    parts = urlsplit(absolute)
    if parts.scheme not in {"http", "https"} or _domain(absolute) != domain:
        return None
    if not PRODUCT_PATHS[domain].search(parts.path):
        return None
    return catalog._canonical(absolute)


def _price(value: Any) -> float | None:
    if isinstance(value, dict):
        for key in ("salePrice", "priceAsNumber", "sale_price", "price", "lowPrice"):
            if value.get(key) not in (None, ""):
                amount = _price(value[key])
                if amount:
                    return amount
        return None
    try:
        amount = float(str(value).replace("£", "").replace(",", "").strip())
    except (TypeError, ValueError):
        match = PRICE.search(str(value or ""))
        amount = float(match.group(1).replace(",", "")) if match else 0
    return amount if amount > 0 else None


def _image(value: Any, page_url: str) -> str | None:
    candidates = []
    if isinstance(value, str):
        candidates.append(value)
    elif isinstance(value, list):
        for item in value:
            image = _image(item, page_url)
            if image:
                candidates.append(image)
    elif isinstance(value, dict):
        for key in ("src", "url", "href", "contentUrl", "primaryImage"):
            if value.get(key):
                image = _image(value[key], page_url)
                if image:
                    candidates.append(image)
        attributes = value.get("attributes")
        if isinstance(attributes, dict) and attributes.get("media_original_url"):
            image = _image(attributes["media_original_url"], page_url)
            if image:
                candidates.append(image)
    for value in candidates:
        absolute = urljoin(page_url, value)
        if urlsplit(absolute).scheme == "https":
            host = (urlsplit(absolute).hostname or "").lower()
            if "asos-media.com" in host:
                absolute = re.sub(r"\$n_\d+w\$", "$n_640w$", absolute)
                absolute = re.sub(r"([?&]wid=)\d+", r"\g<1>640", absolute)
            elif "barbour.com" in host:
                absolute = re.sub(r"([?&]sw=)\d+", r"\g<1>1000", absolute)
            elif "charlestyrwhitt.com" in host:
                absolute = re.sub(r"([?&]sw=)\d+", r"\g<1>750", absolute)
                absolute = re.sub(r"([?&]cw=)\d+", r"\g<1>750", absolute)
                absolute = re.sub(r"([?&]ch=)\d+", r"\g<1>950", absolute)
            return absolute
    return None


def _record(title, price, image, url, page_url, domain, gender, brand=None):
    title = str(title or "").strip()
    amount = _price(price)
    canonical = _canonical(str(url or ""), page_url, domain)
    image_url = _image(image, page_url)
    if not title or amount is None or not canonical or not image_url:
        return None
    product = {
        "@type": "Product",
        "name": title,
        "url": canonical,
        "image": image_url,
        "brand": brand or catalog._HOUSE_BRANDS.get(domain),
        "offers": {"@type": "Offer", "priceCurrency": "GBP", "price": amount},
    }
    return catalog._record(product, product, canonical, domain, gender, f"listing:{domain}")


def _script_payloads(html: str):
    for match in re.finditer(r"<script\b([^>]*)>(.*?)</script\s*>", html, re.I | re.S):
        attrs, body = match.group(1), match.group(2).strip()
        if not body:
            continue
        if "application/ld+json" in attrs.lower():
            try:
                yield json.loads(body)
            except json.JSONDecodeError:
                continue
        if re.search(r"\bid=[\"']__NEXT_DATA__[\"']", attrs, re.I):
            try:
                yield json.loads(body)
            except json.JSONDecodeError:
                continue
        if "data-nuxt-data" in attrs.lower():
            try:
                yield {"__nuxt_flat": json.loads(body)}
            except json.JSONDecodeError:
                continue
        for pattern in (
            r"(?:window\.)?(?:__INITIAL_STATE__|__NEXT_DATA__|__PRELOADED_STATE__)\s*=\s*({.*?});",
        ):
            for found in re.finditer(pattern, body, re.S):
                try:
                    yield json.loads(found.group(1))
                except json.JSONDecodeError:
                    continue


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _john_flat_products(flat: list[Any]):
    wrappers = {"ShallowReactive", "Reactive", "Ref", "ShallowRef"}

    def dereference(value, depth=0):
        if depth > 8:
            return None
        if isinstance(value, list) and value and isinstance(value[0], str) and value[0] in wrappers:
            return dereference(value[-1], depth + 1)
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < len(flat):
            raw = flat[value]
            if isinstance(raw, list) and raw and isinstance(raw[0], str) and raw[0] in wrappers:
                return dereference(raw[-1], depth + 1)
            return raw
        return value

    def decode_field(value, encoded, depth=0):
        if depth > 4:
            return value
        if encoded:
            value = dereference(value)
        if isinstance(value, list):
            if value and isinstance(value[0], str) and value[0] in wrappers:
                return decode_field(value[-1], True, depth + 1)
            return [decode_field(part, encoded, depth + 1) for part in value]
        if isinstance(value, dict):
            return {
                key: decode_field(part, encoded, depth + 1)
                for key, part in value.items()
            }
        return value

    for container in flat:
        if not isinstance(container, dict) or "products" not in container:
            continue
        products = decode_field(container["products"], True)
        if not isinstance(products, list):
            continue
        for product in products:
            item = decode_field(product, isinstance(product, int))
            if isinstance(item, dict):
                yield item


def _itemlist_names(payloads, page_url, domain):
    names = {}
    for payload in payloads:
        for node in _walk(payload):
            if str(node.get("@type", "")).lower() != "itemlist":
                continue
            for entry in node.get("itemListElement", []):
                item = entry.get("item") if isinstance(entry, dict) else None
                if not isinstance(item, dict) and isinstance(entry, dict):
                    item = entry
                if not isinstance(item, dict):
                    continue
                url = _canonical(item.get("@id") or item.get("url") or "", page_url, domain)
                if url and item.get("name"):
                    name = str(item["name"]).strip()
                    if domain == "barbour.com":
                        words = set(re.findall(r"[a-z]+", name.lower()))
                        slug_words = set(re.findall(r"[a-z]+", urlsplit(url).path.lower()))
                        if name.isupper() or not words.intersection(slug_words):
                            continue
                    names[url] = name
    return names


def _itemlist_urls(payloads, page_url, domain):
    urls = set()
    for payload in payloads:
        for node in _walk(payload):
            if str(node.get("@type", "")).lower() != "itemlist":
                continue
            for entry in node.get("itemListElement", []):
                item = entry.get("item") if isinstance(entry, dict) else None
                if not isinstance(item, dict) and isinstance(entry, dict):
                    item = entry
                if isinstance(item, dict):
                    url = _canonical(
                        item.get("@id") or item.get("url") or "", page_url, domain
                    )
                    if url:
                        urls.add(url)
    return urls


def _barbour_title(url):
    slug = urlsplit(url).path.rsplit("/", 1)[-1]
    slug = re.sub(r"\.html$", "", slug, flags=re.I)
    slug = re.sub(r"-[A-Z]{2,}[A-Z0-9]*$", "", slug)
    return " ".join(
        "-".join(part.capitalize() for part in word.split("-"))
        for word in slug.split("-")
    )


def _gender_mismatch(item, gender):
    fields = (
        "gender", "audience", "targetAudience", "department",
        "categoryUri", "categoryName", "categories", "categoryUris",
        "product_gender", "product_gender_value",
    )

    def flatten(value):
        if isinstance(value, dict):
            for key, child in value.items():
                yield str(key)
                yield from flatten(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                yield from flatten(child)
        elif value is not None:
            yield str(value)

    text = " ".join(part for key in fields for part in flatten(item.get(key))).lower()
    if gender == "m":
        return bool(re.search(r"\b(?:women|womens|female|ladies)\w*", text))
    return bool(re.search(r"\b(?:men|mens|male|gentlemen)\w*", text))


def _listing_tile_urls(html, page_url, domain):
    tree = _Tree()
    tree.feed(html)
    urls = set()
    for anchor in tree.root.descendants("a"):
        url = _canonical(anchor.attrs.get("href") or "", page_url, domain)
        if not url:
            continue
        if domain == "johnsmedley.com" and "data-product-card" not in anchor.attrs:
            continue
        node = anchor
        for _ in range(7):
            if node.descendants("img") and PRICE.search(node.text()):
                urls.add(url)
                break
            if not node.parent:
                break
            node = node.parent
    return urls


def _structured_metadata(payloads, page_url, domain):
    by_url, by_id = {}, {}
    for payload in payloads:
        if "__nuxt_flat" in payload:
            continue
        for item in _walk(payload):
            title = item.get("name") or item.get("title")
            if not title or not any(
                item.get(key) not in (None, "")
                for key in ("price", "priceAsNumber", "salePrice", "images", "image")
            ):
                continue
            metadata = {
                "title": str(title),
                "brand": item.get("brandName") or item.get("brand"),
                "salePrice": item.get("salePrice"),
                "color": (
                    item.get("color") or item.get("colour")
                    or item.get("colorName") or item.get("colourName")
                ),
            }
            raw_url = (
                item.get("url") or item.get("href") or item.get("canonical")
                or item.get("path")
            )
            canonical = _canonical(raw_url, page_url, domain) if raw_url else None
            if canonical:
                by_url[canonical] = metadata
            for key in ("id", "product", "sku", "productId"):
                value = item.get(key)
                if value not in (None, ""):
                    by_id[str(value)] = metadata
    return by_url, by_id


def _burberry_products(payloads, page_url, gender):
    records = []
    for payload in payloads:
        if "__nuxt_flat" in payload:
            continue
        for item in _walk(payload):
            content = item.get("content")
            price = item.get("price")
            if not isinstance(content, dict) or not isinstance(price, dict):
                continue
            current = price.get("current")
            if not isinstance(current, dict) or current.get("currency") != "GBP":
                continue
            media = item.get("media")
            image = media.get("defaults", {}).get("image") if isinstance(media, dict) else None
            if isinstance(image, dict):
                image = image.get("imageDefault") or image.get("key")
            records.append(
                _record(
                    content.get("title") or content.get("defaultTitle"),
                    current.get("value"),
                    image,
                    item.get("url"),
                    page_url,
                    "burberry.com",
                    gender,
                    "Burberry",
                )
            )
    return [record for record in records if record]


def _append_color(title, color):
    if isinstance(color, dict):
        color = color.get("name") or color.get("label") or color.get("value")
    if isinstance(color, list):
        color = next((item for item in color if isinstance(item, str)), "")
    color = str(color or "").strip()
    if color and color.casefold() not in title.casefold():
        return f"{title} – {color}"
    return title


def _tile_field(node, class_token):
    for tag in ("div", "span", "p", "a"):
        for child in node.descendants(tag):
            if class_token in (child.attrs.get("class") or "").split():
                value = child.text().strip()
                if value:
                    return value
    return ""


def _tile_image(node, page_url):
    for tag in ("img", "source"):
        for image in node.descendants(tag):
            for key in (
                "src", "data-src", "data-lazy-src", "data-original",
                "srcset", "data-srcset",
            ):
                value = image.attrs.get(key)
                if value:
                    candidate = value.split(",")[0].strip().split(" ")[0]
                    absolute = _image(candidate, page_url)
                    if absolute:
                        return absolute
    return None


def _ralph_listing_anchor(anchor):
    if not any(
        token in (anchor.attrs.get("class") or "").split()
        for token in ("js-pdp-link", "product-name-link")
    ):
        return False
    node = anchor
    for _ in range(9):
        classes = (node.attrs.get("class") or "").split()
        if "pdp-simple-product-tile" in classes:
            return False
        if "js-product-tile" in classes:
            return True
        if not node.parent:
            break
        node = node.parent
    return False


def _product_id(url):
    match = re.search(r"(?:-|/)(\d{5,})(?:/|$)", urlsplit(url).path)
    return match.group(1) if match else ""


def _clean_stories_title(value):
    value = re.sub(
        r"\b(?:chevron-right|chevron-left|favourite-inactive|plus)\b", " ", value,
        flags=re.I,
    )
    value = re.sub(r"\+\s*\d+\b|\bnew\b", " ", value, flags=re.I)
    value = re.sub(
        r"\b(?:100%\s*)?(?:cotton|wool|cashmere|merino|alpaca)(?:[-/& ]"
        r"(?:cotton|wool|cashmere|merino|alpaca))*\b\s*$",
        " ",
        value,
        flags=re.I,
    )
    return re.sub(r"\s+", " ", value).strip(" ,")


def _john_smedley_title(anchor):
    parts = []
    for span in anchor.descendants("span"):
        if "functional-f1" not in (span.attrs.get("class") or "").split():
            continue
        value = re.sub(r"\s+", " ", span.text()).strip()
        if value and value not in parts:
            parts.append(value)
    return " ".join(parts)


def _clean_tile_title(value):
    value = re.sub(r"\s*-\s*view\s+\d+\b", "", value, flags=re.I)
    return re.sub(r"\s+", " ", value).strip(" ,-")


def _mango_title(url, text):
    parts = [part for part in urlsplit(url).path.split("/") if part]
    for index, part in enumerate(parts):
        if part.isdigit() and len(part) >= 5 and index:
            words = parts[index - 1].split("-")
            pattern = r"\b" + r"[\s-]+".join(re.escape(word) for word in words) + r"\b"
            match = re.search(pattern, text, re.I)
            if match:
                return match.group(0).capitalize()
            return " ".join(words).capitalize()
    return ""


def _tile_records(html, page_url, domain, gender, names, metadata_by_url, metadata_by_id):
    tree = _Tree()
    tree.feed(html)
    records = []
    for anchor in tree.root.descendants("a"):
        if domain == "ralphlauren.co.uk" and not _ralph_listing_anchor(anchor):
            continue
        url = _canonical(anchor.attrs.get("href") or "", page_url, domain)
        if not url:
            continue
        node = anchor
        tile = None
        tile_brand = ""
        for _ in range(7):
            text = node.text()
            images = node.descendants("img")
            image = next(
                (img.attrs.get("src") or img.attrs.get("data-src") or
                 img.attrs.get("data-lazy-src") or img.attrs.get("data-original") or
                 (img.attrs.get("srcset") or img.attrs.get("data-srcset") or "").split(" ")[0]
                 for img in images
                 if any(img.attrs.get(key) for key in (
                     "src", "data-src", "data-lazy-src", "data-original",
                     "srcset", "data-srcset",
                 ))),
                None,
            )
            if not image and domain == "ralphlauren.co.uk":
                image = _tile_image(node, page_url)
            price = PRICE.search(text)
            if image and price:
                tile = (text, price.group(1), image)
                tile_brand = _tile_field(node, "product-brand")
                break
            if not node.parent:
                break
            node = node.parent
        if not tile:
            continue
        text, price, image = tile
        metadata = metadata_by_url.get(url) or metadata_by_id.get(_product_id(url), {})
        title = metadata.get("title") or names.get(url)
        brand = metadata.get("brand") or tile_brand
        price = metadata.get("salePrice") or price
        if not title and domain == "mango.com":
            title = _mango_title(url, text)
        if not title and domain == "asos.com":
            title = next(
                (
                    img.attrs.get("alt")
                    for img in node.descendants("img")
                    if img.attrs.get("alt")
                ),
                "",
            )
        if not title and domain == "johnsmedley.com":
            title = _john_smedley_title(anchor)
        if not title:
            title = PRICE.sub("", text)
            title = re.sub(
                r"\b(?:quick add|more colours|selling fast|add to wishlist)\b.*",
                "",
                title,
                flags=re.I,
            )
            title = re.sub(r"\bprice\b\s*$", "", title, flags=re.I)
            title = re.sub(r"\s+", " ", title).strip(" ,")
        else:
            title = PRICE.sub("", title)
            title = re.sub(r"\bprice\b\s*$", "", title, flags=re.I).strip(" ,")
        if domain == "stories.com":
            title = _clean_stories_title(title)
        else:
            title = _clean_tile_title(title)
        if domain in {"arket.com", "cos.com", "stories.com"}:
            title = _append_color(title, metadata.get("color"))
        if domain == "ralphlauren.co.uk":
            brand = brand or _tile_field(node, "product-brand")
            brand = brand or _tile_field(node, "product-brand-name")
            if not brand:
                text_lower = node.text().lower()
                brand = next(
                    (
                        candidate for candidate in
                        ("Polo Ralph Lauren", "Purple Label", "Lauren", "RRL")
                        if candidate.lower() in text_lower
                    ),
                    None,
                )
            title = (
                _tile_field(node, "product-name")
                or _tile_field(node, "product-name-link")
                or title
            )
        if domain == "barbour.com":
            title_words = set(re.findall(r"[a-z]+", title.lower()))
            url_words = set(re.findall(r"[a-z]+", urlsplit(url).path.lower()))
            if title.isupper() or len(title) > 120 or not title_words.intersection(url_words):
                title = _barbour_title(url)
        record = _record(title, price, image, url, page_url, domain, gender, brand)
        if record:
            records.append(record)
    return records


def _next_products(
    payloads, page_url, domain, gender, metadata_by_url, metadata_by_id,
    gender_mismatch_urls,
):
    records = []
    for payload in payloads:
        objects = (
            _john_flat_products(payload["__nuxt_flat"])
            if "__nuxt_flat" in payload else _walk(payload)
        )
        for item in objects:
            if not isinstance(item, dict) or not (item.get("name") or item.get("title")):
                continue
            url = (
                item.get("url") or item.get("uri") or item.get("href")
                or item.get("canonical") or item.get("path")
            )
            if domain == "cos.com" and item.get("uri") and item.get("categoryUri"):
                url = f"/en-gb/{item['categoryUri']}/product/{item['uri']}"
            elif domain == "arket.com" and item.get("uri"):
                url = f"/en-gb/product/{item['uri']}/"
            elif domain == "stories.com":
                url = item.get("href") or url
            if not url and item.get("id") and item.get("categoryUri"):
                slug = re.sub(r"[^a-z0-9]+", "-", str(item.get("name")).lower()).strip("-")
                url = f"/en-gb/{item['categoryUri']}/product/{slug}-{item['id']}"
            canonical = _canonical(url, page_url, domain) if url else None
            if domain == "johnsmedley.com" and _gender_mismatch(item, gender):
                if canonical:
                    gender_mismatch_urls.add(canonical)
                continue
            if not any(
                item.get(k) not in (None, "")
                for k in ("price", "priceAsNumber", "salePrice")
            ):
                continue
            price = item.get("salePrice") or item.get("priceAsNumber") or item.get("price")
            metadata = {
                "title": str(item.get("name") or item.get("title")),
                "brand": item.get("brandName") or item.get("brand"),
                "salePrice": item.get("salePrice"),
                "color": (
                    item.get("color") or item.get("colour")
                    or item.get("colorName") or item.get("colourName")
                ),
            }
            if canonical:
                metadata_by_url[canonical] = metadata
            for key in ("id", "product", "sku", "productId"):
                value = item.get(key)
                if value not in (None, ""):
                    metadata_by_id[str(value)] = metadata
            image = (
                item.get("primaryImage") or item.get("images") or item.get("mediaObjects")
                or item.get("image") or item.get("imageUrl")
            )
            title = str(item.get("name") or item.get("title"))
            if domain in {"arket.com", "cos.com", "stories.com"}:
                title = _append_color(title, metadata.get("color"))
            record = _record(
                title,
                price,
                image,
                url,
                page_url,
                domain,
                gender,
                item.get("brandName") or item.get("brand"),
            )
            if record:
                records.append(record)
    return records


def parse_listing(html: str, page_url: str, domain: str, gender: str) -> list[dict]:
    domain = _domain(domain)
    if not html or domain not in DOMAINS:
        return []
    payloads = list(_script_payloads(html))
    names = _itemlist_names(payloads, page_url, domain)
    metadata_by_url, metadata_by_id = _structured_metadata(payloads, page_url, domain)
    gender_mismatch_urls = set()
    records = _burberry_products(payloads, page_url, gender) if domain == "burberry.com" else []
    records.extend(_next_products(
        payloads, page_url, domain, gender, metadata_by_url, metadata_by_id,
        gender_mismatch_urls,
    ))
    records.extend(
        _tile_records(
            html, page_url, domain, gender, names, metadata_by_url, metadata_by_id
        )
    )
    if domain == "barbour.com":
        listing_urls = _itemlist_urls(payloads, page_url, domain)
        if listing_urls:
            records = [
                record for record in records
                if record.get("source_url") in listing_urls
            ]
    if domain == "johnsmedley.com":
        listing_urls = _listing_tile_urls(html, page_url, domain)
        if listing_urls:
            records = [
                record for record in records
                if record.get("source_url") in listing_urls
                and record.get("source_url") not in gender_mismatch_urls
            ]
    deduped = {}
    for record in records:
        url = record.get("source_url")
        if url:
            deduped.setdefault(url, record)
    return list(deduped.values())


def infer_category(title: str, page_url: str = "") -> str:
    value = str(title or "").lower()
    for pattern, category in (
        (r"\b(?:shoes?|boots?|loafers?|trainers?|sneakers?|heels?|sandals?)\b", "shoes"),
        (
            r"\b(?:bags?|backpacks?|belts?|scarves?|scarfs?|hats?|caps?|"
            r"gloves?|wallets?)\b",
            "accessory",
        ),
        (r"\b(?:jackets?|coats?|parkas?|blazers?|gilets?)\b", "outerwear"),
        (r"\b(?:dresses|dress|gowns?)\b", "dress"),
        (r"\b(?:trousers?|chinos?|jeans?|denim|skirts?|shorts?)\b", "bottom"),
        (r"\b(?:shirts?|blouses?|tops?|tees?|t-shirts?|polos?)\b", "top"),
        (r"\b(?:knitwear|knits?|jumpers?|sweaters?|cardigans?|pullovers?)\b", "knitwear"),
        (r"\b(?:suits?|tailoring|tailored)\b", "tailoring"),
    ):
        if re.search(pattern, value):
            return category
    path = page_url.lower()
    for words, category in (
        (("knit", "jumper", "sweater"), "knitwear"),
        (("shirt", "top"), "top"),
        (("trouser", "jean", "denim"), "bottom"),
        (("jacket", "coat", "outerwear"), "outerwear"),
        (("dress", "skirt"), "dress"),
        (("suit", "tailor"), "tailoring"),
    ):
        if any(word in path for word in words):
            return category
    return "clothing"
