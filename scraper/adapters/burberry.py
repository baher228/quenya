import json
from urllib.parse import urlencode, urljoin

from .common import ShopSession, make_row, request_succeeded, unique_rows

ROOT_IDS = {"m": "cat10011063", "w": "cat1350151"}


def _walk_nav(value, parents, found):
    if isinstance(value, dict):
        category_id = value.get("categoryId")
        chain = parents + ([category_id] if category_id else [])
        href = value.get("linkHref")
        if category_id and isinstance(href, str) and href.startswith("/l/"):
            found.setdefault(href, chain)
        for child in value.values():
            _walk_nav(child, chain, found)
    elif isinstance(value, list):
        for child in value:
            _walk_nav(child, parents, found)


def _preloaded(html):
    marker = "window.__PRELOADED_STATE__ ="
    at = html.find(marker)
    if at < 0:
        return {}
    tail = html[at + len(marker) :].lstrip()
    try:
        data, _ = json.JSONDecoder().raw_decode(tail)
        return data
    except ValueError:
        return {}


def scrape(shop: dict, session: ShopSession, probe: bool = False):
    pages = shop.get("pages", [])[: 1 if probe else None]
    if not pages:
        return []
    nav_url = pages[0]["url"]
    result = session.get(nav_url)
    if result is None:
        shop["_status"] = "robots"
        shop["_reason"] = "navigation disallowed by robots.txt"
        return []
    html, nav_final, _ = result
    if not request_succeeded(session):
        shop["_status"] = "error"
        shop["_reason"] = "Burberry navigation request failed"
        return []
    nav = _preloaded(html).get("navigation", {})
    chains = {}
    _walk_nav(nav, [], chains)
    rows = []
    for page in pages:
        gender, category_url = page["gender"], page["url"]
        location = "/" + "/".join(
            [ROOT_IDS[gender]] + chains.get(page.get("path") or category_url, [])
        )
        for offset in range(0, int(shop.get("max_pages", 3)) * 20, 20):
            endpoint = urljoin(nav_final, "/web-api/pages/products")
            query = urlencode(
                {
                    "location": location,
                    "offset": offset,
                    "limit": 20,
                    "language": "en",
                    "country": "GB",
                }
            )
            fetched = session.get(endpoint + "?" + query)
            if fetched is None:
                shop["_status"] = "robots"
                shop["_reason"] = "product API disallowed by robots.txt"
                break
            if not request_succeeded(session):
                continue
            body, final_url, _ = fetched
            try:
                data = json.loads(body)["data"]
                products = data["products"][0]["items"]
            except (ValueError, KeyError, IndexError, TypeError):
                break
            page_rows = []
            for product in products:
                current = (product.get("price") or {}).get("current") or {}
                media = (product.get("media") or {}).get("defaults") or {}
                image = (media.get("image") or {}).get("imageDefault") or ""
                title = str((product.get("content") or {}).get("title") or "").strip()
                if current.get("currency") != "GBP" or not title:
                    continue
                types = product.get("types") or {}
                if (
                    types.get("isSoldOut")
                    or types.get("isComingSoon")
                    or types.get("isRegisterForInterest")
                ):
                    continue
                image = image.split("?")[0] + "?$BBY_V2_SL_3x4$&wid=640&hei=852"
                row = make_row(
                    shop,
                    title,
                    float(current.get("value") or 0),
                    image,
                    urljoin(final_url, product.get("url", "")),
                    gender,
                    category_url,
                    {
                        "availability": "InStock",
                        "price_source": "burberry_web_api_gbp",
                        "product_id": product.get("id"),
                        "on_markdown": product.get("isOnMarkdown"),
                    },
                )
                if row:
                    page_rows.append(row)
            rows.extend(page_rows)
            session.emit(page_rows)
            if (
                probe
                or not products
                or offset + 20 >= int((data.get("productsInfo") or {}).get("total") or 0)
            ):
                break
    return unique_rows(rows)
