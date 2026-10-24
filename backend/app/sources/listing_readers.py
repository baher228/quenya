import json
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

from app.sources import catalog

DOMAINS = {
    "cos.com", "arket.com", "stories.com", "barbour.com",
    "charlestyrwhitt.com", "johnsmedley.com", "asos.com", "mango.com",
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


def _record(title, price, image, url, page_url, domain, gender):
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
            r"(?:window\.)?(?:__INITIAL_STATE__|__NEXT_DATA__)\s*=\s*({.*?});",
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


def _nuxt(flat: list[Any]):
    cache = {}

    def decode(value, depth=0):
        if depth > 35:
            return None
        if isinstance(value, int) and 0 <= value < len(flat):
            if value not in cache:
                raw = flat[value]
                if isinstance(raw, list) and raw and isinstance(raw[0], str) and raw[0] in {
                    "ShallowReactive", "Reactive", "Ref", "ShallowRef",
                }:
                    raw = raw[-1]
                cache[value] = decode(raw, depth + 1)
            return cache[value]
        if isinstance(value, dict):
            return {key: decode(item, depth + 1) for key, item in value.items()}
        if isinstance(value, list):
            return [decode(item, depth + 1) for item in value]
        return value

    yield from _walk(decode(flat))


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
                    names[url] = item["name"]
    return names


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


def _tile_records(html, page_url, domain, gender, names):
    tree = _Tree()
    tree.feed(html)
    records = []
    for anchor in tree.root.descendants("a"):
        url = _canonical(anchor.attrs.get("href") or "", page_url, domain)
        if not url:
            continue
        node = anchor
        tile = None
        for _ in range(7):
            text = node.text()
            images = node.descendants("img")
            image = next(
                (img.attrs.get("src") or img.attrs.get("data-src") or
                 (img.attrs.get("srcset") or "").split(" ")[0]
                 for img in images
                 if img.attrs.get("src") or img.attrs.get("data-src") or img.attrs.get("srcset")),
                None,
            )
            price = PRICE.search(text)
            if image and price:
                tile = (text, price.group(1), image)
                break
            if not node.parent:
                break
            node = node.parent
        if not tile:
            continue
        text, price, image = tile
        title = names.get(url)
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
        record = _record(title, price, image, url, page_url, domain, gender)
        if record:
            records.append(record)
    return records


def _next_products(payloads, page_url, domain, gender):
    records = []
    for payload in payloads:
        objects = _nuxt(payload["__nuxt_flat"]) if "__nuxt_flat" in payload else _walk(payload)
        for item in objects:
            if not isinstance(item, dict) or not (item.get("name") or item.get("title")):
                continue
            if not any(
                item.get(k) not in (None, "")
                for k in ("price", "priceAsNumber", "salePrice")
            ):
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
            price = item.get("salePrice") or item.get("priceAsNumber") or item.get("price")
            image = (
                item.get("primaryImage") or item.get("images") or item.get("mediaObjects")
                or item.get("image") or item.get("imageUrl")
            )
            record = _record(
                item.get("name") or item.get("title"),
                price,
                image,
                url,
                page_url,
                domain,
                gender,
            )
            if record:
                records.append(record)
    return records


def parse_listing(html: str, page_url: str, domain: str, gender: str) -> list[dict]:
    domain = _domain(domain)
    if not html or domain not in DOMAINS:
        return []
    payloads = list(_script_payloads(html))
    records = _next_products(payloads, page_url, domain, gender)
    names = _itemlist_names(payloads, page_url, domain)
    records.extend(_tile_records(html, page_url, domain, gender, names))
    deduped = {}
    for record in records:
        url = record.get("source_url")
        if url:
            deduped.setdefault(url, record)
    return list(deduped.values())
