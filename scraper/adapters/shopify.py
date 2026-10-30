import json
import re
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin

from .common import (
    ShopSession,
    accepts,
    eur_to_gbp,
    make_row,
    request_succeeded,
    unique_rows,
)
from .tees import tee_gender


def gender_for(product: dict, rule: str, default: str = "m") -> str:
    text = " ".join(
        [str(product.get("title") or ""), str(product.get("product_type") or "")]
        + [str(tag) for tag in product.get("tags", [])]
    ).lower()
    product_type = str(product.get("product_type") or "").lower()
    if rule == "women":
        return "w"
    if rule == "men":
        return "m"
    if rule == "belstaff":
        return "w" if product_type.endswith(" - w") else "m"
    if rule == "sunspel":
        return "w" if product_type.startswith("womens") else "m"
    if rule == "selfportrait":
        return "w"
    if rule == "represent" and any(word in text for word in ("women", "womens", "female")):
        return "w"
    return default


def _available_variant(product: dict):
    variants = [
        variant
        for variant in product.get("variants", [])
        if isinstance(variant, dict) and variant.get("available") is True
    ]
    priced = []
    for variant in variants:
        try:
            price = float(variant.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if price > 0:
            priced.append((price, variant))
    return min(priced, key=lambda item: item[0]) if priced else (0.0, None)


def _products(data):
    products = data.get("products", []) if isinstance(data, dict) else []
    return products if isinstance(products, list) else []


class _PricePageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta = {}
        self.json_ld = []
        self._capturing_json_ld = False
        self._script_parts = []

    def handle_starttag(self, tag, attrs):
        attributes = {
            str(key).lower(): value for key, value in attrs if key and value is not None
        }
        if tag.lower() == "meta":
            key = (
                attributes.get("property")
                or attributes.get("name")
                or attributes.get("itemprop")
            )
            if key and attributes.get("content"):
                self.meta[str(key).lower()] = attributes["content"]
        elif tag.lower() == "script" and "application/ld+json" in str(
            attributes.get("type") or ""
        ).lower():
            self._capturing_json_ld = True
            self._script_parts = []

    def handle_data(self, data):
        if self._capturing_json_ld:
            self._script_parts.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "script" and self._capturing_json_ld:
            self.json_ld.append("".join(self._script_parts))
            self._capturing_json_ld = False


def _price_value(value):
    try:
        price = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, TypeError, ValueError):
        return None
    return price if price.is_finite() and price > 0 else None


def _jsonld_gbp_offers(value):
    if isinstance(value, dict):
        currency = str(value.get("priceCurrency") or "").upper()
        if currency == "GBP":
            price = _price_value(value.get("price", value.get("lowPrice")))
            if price is not None:
                availability = str(value.get("availability") or "").lower()
                yield price, "outofstock" not in availability
        for nested in value.values():
            yield from _jsonld_gbp_offers(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _jsonld_gbp_offers(nested)


def _localized_gbp_price(html: str) -> Decimal | None:
    parser = _PricePageParser()
    parser.feed(html)
    offers = []
    for script in parser.json_ld:
        try:
            offers.extend(_jsonld_gbp_offers(json.loads(script)))
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    in_stock = [price for price, available in offers if available]
    if in_stock:
        return min(in_stock)
    if offers:
        return min(price for price, _ in offers)

    currencies = [
        value.upper()
        for key, value in parser.meta.items()
        if key.endswith(("price:currency", "price.currency"))
    ]
    amounts = [
        value
        for key, value in parser.meta.items()
        if key.endswith(("price:amount", "price.amount"))
    ]
    if "GBP" not in currencies:
        return None
    for amount in amounts:
        price = _price_value(amount)
        if price is not None:
            return price
    return None


def _request_capacity_reached(session):
    count = getattr(session, "count", None)
    maximum = getattr(session, "max_requests", None)
    return count is not None and maximum is not None and count >= maximum


def _colour_option_position(product):
    options = product.get("options") or []
    for index, option in enumerate(options):
        if not isinstance(option, dict) or not re.search(
            r"colou?r|colorway|shade",
            str(option.get("name") or ""),
            re.IGNORECASE,
        ):
            continue
        try:
            return int(option.get("position") or index + 1)
        except (TypeError, ValueError):
            return index + 1
    return None


def _single_available_colour(product):
    option_position = _colour_option_position(product)
    if option_position is None:
        return None
    colours = {
        str(variant.get(f"option{option_position}") or "").strip()
        for variant in product.get("variants", [])
        if isinstance(variant, dict)
        and variant.get("available") is True
        and str(variant.get(f"option{option_position}") or "").strip()
    }
    return next(iter(colours)) if len(colours) == 1 else None


def _colour_rows(product):
    option_position = _colour_option_position(product)
    if option_position is None:
        return None

    groups = {}
    for variant in product.get("variants", []):
        if not isinstance(variant, dict) or variant.get("available") is not True:
            continue
        try:
            price = float(variant.get("price") or 0)
        except (TypeError, ValueError):
            continue
        colour = str(variant.get(f"option{option_position}") or "").strip()
        if price > 0 and colour:
            groups.setdefault(colour, []).append((price, variant))
    if len(groups) < 2:
        return None

    images = product.get("images") or []
    result = []
    for colour, variants in groups.items():
        variants.sort(key=lambda item: item[0])
        price, cheapest = variants[0]
        if not cheapest.get("id"):
            return None
        image = ""
        for _, variant in variants:
            featured = variant.get("featured_image")
            if isinstance(featured, dict) and featured.get("src"):
                image = str(featured["src"])
                break
        if not image:
            variant_ids = {str(variant.get("id")) for _, variant in variants if variant.get("id")}
            for item in images:
                if not isinstance(item, dict):
                    continue
                image_ids = {str(value) for value in item.get("variant_ids", [])}
                if variant_ids.intersection(image_ids) and item.get("src"):
                    image = str(item["src"])
                    break
        if not image:
            return None
        if image.startswith("http://"):
            image = "https://" + image[7:]
        result.append(
            {
                "colour": colour,
                "price": price,
                "variant": cheapest,
                "image": image,
            }
        )
    if len({item["image"] for item in result}) == 1:
        return None
    return result


def scrape(shop: dict, session: ShopSession, probe: bool = False):
    host = shop["host"]
    price_country = str(shop.get("price_country") or "").upper()
    if price_country and price_country != "GB":
        shop["_status"] = "unsupported_price_country"
        shop["_reason"] = f"unsupported localized price country {price_country!r}"
        return []
    currency = str(shop.get("currency") or "GBP").upper()
    if not price_country and currency not in {"GBP", "EUR"}:
        shop["_status"] = "non_gbp"
        shop["_reason"] = f"unsupported configured currency {currency!r}"
        return []
    fx_rate = None
    if not price_country and currency == "EUR":
        try:
            fx_rate = Decimal(str(shop["_fx_rate"]))
            if not fx_rate.is_finite() or fx_rate <= 0:
                raise InvalidOperation
        except (KeyError, InvalidOperation, TypeError, ValueError):
            shop["_status"] = "fx_unavailable"
            shop["_reason"] = "EUR-to-GBP rate unavailable; shop skipped"
            return []
    meta_url = f"https://{host}/meta.json"
    fetched = session.get(meta_url)
    if fetched is None:
        shop["_status"] = "robots"
        shop["_reason"] = "meta.json disallowed by robots.txt"
        return []
    if not request_succeeded(session):
        shop["_status"] = "error"
        shop["_reason"] = "meta.json request failed"
        return []
    meta_text, _, _ = fetched
    meta = json.loads(meta_text)
    meta_currency = str(meta.get("currency", "")).upper()
    if not price_country and meta_currency != currency:
        shop["_status"] = "non_gbp"
        shop["_reason"] = f"meta.json currency={meta_currency!r}"
        return []
    price_report = None
    if price_country:
        price_report = {
            "country": price_country,
            "currency_required": "GBP",
            "checked": 0,
            "kept": 0,
            "skipped_no_gbp": 0,
            "unverified": 0,
        }
        shop["_price_country_report"] = price_report
    base_domain = str(meta.get("domain") or host).lower()
    product_base = shop.get("product_base") or f"https://{base_domain}"
    if not product_base.startswith("https://"):
        product_base = "https://" + product_base
    category_set = shop.get("_category_set")
    accessories = category_set == "accessories"
    tees = category_set == "tees"
    collections = (
        [None]
        if accessories
        else shop.get("tee_collections") or [None]
        if tees
        else shop.get("collections") or [None]
    )
    max_pages = 1 if probe else int(shop.get("max_pages", 20))
    rows = []
    for collection in collections:
        if accessories:
            gender = None
        elif tees:
            gender = collection.get("gender") if collection else None
        else:
            gender = (
                collection.get("gender", shop.get("default_gender", "m"))
                if collection
                else shop.get("default_gender", "m")
            )
        collection_handle = collection.get("handle") if collection else None
        for page in range(1, max_pages + 1):
            if price_country and _request_capacity_reached(session):
                break
            endpoint = (
                f"https://{host}/collections/{collection_handle}/products.json"
                if collection_handle
                else f"https://{host}/products.json"
            )
            url = endpoint + "?" + urlencode({"limit": 250, "page": page})
            fetched = session.get(url)
            if fetched is None:
                shop["_status"] = "robots"
                shop["_reason"] = "products.json disallowed by robots.txt"
                return unique_rows(rows)
            if not request_succeeded(session):
                continue
            response, _, _ = fetched
            products = _products(json.loads(response))
            if probe:
                shop["_probe_products"] = len(products)
            if not products:
                break
            page_rows = []
            for product in products:
                if not isinstance(product, dict):
                    continue
                title = str(product.get("title") or "").strip()
                product_type = str(product.get("product_type") or "")
                tags = product.get("tags") or []
                if isinstance(tags, str):
                    tags = [tag.strip() for tag in tags.split(",")]
                evidence = " ".join([title, product_type, " ".join(map(str, tags))])
                if not title or not accepts(
                    shop,
                    evidence,
                    title=title,
                    product_type=product_type,
                    tags=tags,
                ):
                    continue
                rejected_tags = {"kids", "kid"}
                if not accessories:
                    rejected_tags.update({"jewellery", "jewelry"})
                if tees:
                    rejected_tags.update(
                        {"youth", "junior", "children", "kidswear", "boys", "girls"}
                    )
                if any(str(tag).lower() in rejected_tags for tag in tags):
                    continue
                images = product.get("images") or []
                product_handle = str(product.get("handle") or "")
                product_url = urljoin(
                    product_base.rstrip("/") + "/", f"products/{product_handle}"
                )
                localized_price = None
                if price_country:
                    if not _available_variant(product)[1]:
                        continue
                    if _request_capacity_reached(session):
                        price_report["unverified"] += 1
                        continue
                    localized_url = (
                        product_url
                        + ("&" if "?" in product_url else "?")
                        + urlencode({"country": price_country})
                    )
                    price_report["checked"] += 1
                    try:
                        localized_response = session.get(localized_url)
                    except RuntimeError as exc:
                        if str(exc) != "per-shop request cap reached":
                            raise
                        price_report["checked"] -= 1
                        price_report["unverified"] += 1
                        continue
                    if localized_response is None or not request_succeeded(session):
                        price_report["skipped_no_gbp"] += 1
                        continue
                    localized_price = _localized_gbp_price(localized_response[0])
                    if localized_price is None:
                        price_report["skipped_no_gbp"] += 1
                        continue
                    price_report["kept"] += 1
                colour_rows = _colour_rows(product) if tees else None
                single_colour = (
                    _single_available_colour(product) if tees and not colour_rows else None
                )
                variants = colour_rows or [None]
                for colour_row in variants:
                    if colour_row:
                        price = (
                            float(localized_price) if price_country else colour_row["price"]
                        )
                        variant = colour_row["variant"]
                        image = colour_row["image"]
                    else:
                        price, variant = _available_variant(product)
                        if not variant:
                            continue
                        if price_country:
                            price = float(localized_price)
                        image = (
                            images[0].get("src", "")
                            if images and isinstance(images[0], dict)
                            else ""
                        )
                        if image.startswith("http://"):
                            image = "https://" + image[7:]
                    original_price = price
                    row_url = (
                        product_url
                        + "?"
                        + urlencode({"variant": variant["id"]})
                        if colour_row
                        else product_url
                    )
                    row_title = title
                    colour = colour_row["colour"] if colour_row else single_colour
                    if colour and colour.casefold() not in title.casefold():
                        row_title = f"{title} - {colour}"
                    row_shop = shop
                    if shop.get("brand_from_vendor"):
                        vendor = str(product.get("vendor") or "").strip()
                        if vendor:
                            row_shop = dict(shop, brand=vendor)
                    extra = {
                        "availability": "InStock",
                        "product_id": product.get("id"),
                        "product_type": product_type,
                        "price_source": (
                            "shopify_market_gbp"
                            if price_country
                            else "shopify_variant_gbp"
                            if currency == "GBP"
                            else "shopify_variant_eur_fx"
                        ),
                        "shopify_product": product,
                    }
                    if price_country:
                        extra.update(
                            price_country=price_country,
                            original_currency=meta_currency,
                        )
                    if colour_row:
                        extra.update(
                            colour=colour_row["colour"],
                            variant_id=variant.get("id"),
                        )
                    if not price_country:
                        compare = variant.get("compare_at_price")
                        try:
                            if compare and float(compare) > original_price:
                                extra["compare_at_price"] = float(compare)
                        except (TypeError, ValueError):
                            pass
                    if not price_country and currency == "EUR":
                        price = eur_to_gbp(original_price, fx_rate)
                        extra.update(
                            original_price=original_price,
                            original_currency=currency,
                            fx_rate=float(fx_rate),
                        )
                    row_gender = (
                        tee_gender(shop, product, row_url, gender)
                        if tees
                        else None if accessories
                        else gender_for(product, shop.get("gender_rule", "default"), gender)
                    )
                    row = make_row(
                        row_shop,
                        row_title,
                        price,
                        image,
                        row_url,
                        row_gender,
                        row_url,
                        extra,
                    )
                    if row:
                        page_rows.append(row)
            rows.extend(page_rows)
            session.emit(page_rows)
            if getattr(session, "full", False):
                break
            if probe and not tees:
                break
        if getattr(session, "full", False):
            break
    return unique_rows(rows)
