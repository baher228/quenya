"""Resumable, polite catalogue scrape and tag-before-insert job."""

import argparse
import gzip
import hashlib
import json
import logging
import os
import re
import sys
import threading
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, is_dataclass
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
GLOSSARY_PATH = BACKEND / "app" / "data" / "style_glossary.json"
OUT_ROOT = Path("/home/ubuntu/research/bulk")
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)
VINTED_CATEGORIES = [
    "mens jacket", "womens dress", "jeans", "trainers", "knitwear",
    "hoodie", "coat", "skirt", "boots", "bag",
]
SITEMAP_SHOPS = {
    "boohoo.com": "https://www.boohoo.com",
    "prettylittlething.com": "https://www.prettylittlething.com",
    "hugoboss.com": "https://www.hugoboss.com",
}
SHOPIFY_STORES = [
    "universalworks.co.uk", "adanola.com", "rixo.co.uk", "uk.stussy.com",
    "scampanddude.com", "sunspel.com",
]
APPAREL_WORDS = re.compile(
    r"\b(clothing|apparel|fashion|accessor(?:y|ies)|jewel(?:l?ery|ry)|sunglasses|footwear|"
    r"shoes?|trainers?|boots?|sandals?|"
    r"bags?|jacket|coat|dress|shirt|blouse|trousers|jeans|skirt|shorts?|"
    r"jumper|sweater|knitwear|hoodie|tracksuit|vest|top|t-?shirt|belt|"
    r"scarf|hat|cap|gloves?|wallet|backpack|tights|socks?)\b",
    re.IGNORECASE,
)
NON_APPAREL_WORDS = re.compile(
    r"\b(beauty|skincare|make[- ]?up|cosmetic|lipstick|mascara|foundation|"
    r"concealer|eyeliner|eyeshadow|nail polish|fragrance|perfume|haircare|"
    r"homeware|furniture|kitchenware|kitchen|tableware|drinkware|glassware|"
    r"coasters?|mugs?|plates?|bowls?|cutlery|decor|candles?|bedding|bathroom|"
    r"cushion|pillow|ornament|vase|rug|toy|food|shampoos?|conditioners?|"
    r"body[- ]wash(?:es)?|shower[- ]gels?|hand[- ]creams?|body[- ]lotions?|"
    r"moisturi[sz]ers?|serums?|deodorants?|parfums?|eau[- ]de[- ]toilette|"
    r"eau[- ]de[- ]parfum|gift[- ]sets?|toothbrush(?:es)?|incense)\b",
    re.IGNORECASE,
)
STOCK_WORDS = re.compile(
    r"\b(sold|reserved|closed|unavailable|out of stock)\b", re.IGNORECASE
)
LOG = logging.getLogger("bulk_job")
_ACTIVE_CONNECTION = None
_STATE_LOCK = threading.RLock()
_IMPORT_LOCK = threading.Lock()


class PoliteClient:
    def __init__(self) -> None:
        self.client = httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
            follow_redirects=False,
            timeout=25,
        )
        self.last_request: dict[str, float] = {}

    def get(
        self, url: str, interval: float, redirects: int = 5, scope: str | None = None
    ) -> httpx.Response:
        current = url
        for _ in range(redirects + 1):
            host = (urlsplit(current).hostname or "").lower()
            gates = [host]
            if scope and scope.lower() not in gates:
                gates.append(scope.lower())
            now = time.monotonic()
            delay = max(
                interval - (now - self.last_request.get(gate, 0)) for gate in gates
            )
            if delay > 0:
                time.sleep(delay)
            try:
                response = self.client.get(current)
            finally:
                requested_at = time.monotonic()
                for gate in gates:
                    self.last_request[gate] = requested_at
            if response.status_code not in {301, 302, 303, 307, 308}:
                return response
            location = response.headers.get("location")
            if not location:
                return response
            current = urljoin(current, location)
        return response

    def close(self) -> None:
        self.client.close()


def load_runtime_env() -> None:
    from dotenv import load_dotenv

    load_dotenv("/home/ubuntu/research/supabase.env", override=False)
    load_dotenv("/home/ubuntu/secrets/quenya.env", override=False)
    if os.environ.get("SUPABASE_DB_URL"):
        os.environ["QUENYA_DATABASE_URL"] = os.environ["SUPABASE_DB_URL"]
    os.environ["QUENYA_IMAGE_STORE"] = "supabase"
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))


def read_state(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"done": {}, "counts": {}, "imports_paused": False}


def save_state(path: Path, state: dict) -> None:
    with _STATE_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        temporary.replace(path)


def count_for(state: dict, source: str) -> dict:
    with _STATE_LOCK:
        stats = state.setdefault("counts", {}).setdefault(
            source,
            {
                "scraped": 0,
                "skipped_out_of_stock": 0,
                "skipped_by_reason": {},
                "imported": 0,
                "tag_errors": 0,
                "failed": 0,
                "pending": 0,
            },
        )
        stats.setdefault("skipped_out_of_stock", 0)
        stats.setdefault("skipped_by_reason", {})
        stats.setdefault("scraped", 0)
        stats.setdefault("imported", 0)
        stats.setdefault("tag_errors", 0)
        stats.setdefault("failed", 0)
        stats.setdefault("pending", 0)
        return stats


def add_count(state: dict, source: str, field: str, amount: int = 1) -> None:
    with _STATE_LOCK:
        count_for(state, source)[field] += amount


def add_candidate(state: dict, source: str, amount: int = 1) -> None:
    add_count(state, source, "scraped", amount)


def add_skip(state: dict, source: str, reason: str, amount: int = 1) -> None:
    if amount <= 0:
        return
    with _STATE_LOCK:
        stats = count_for(state, source)
        stats.setdefault("skipped_by_reason", {})[reason] = (
            stats.setdefault("skipped_by_reason", {}).get(reason, 0) + amount
        )
        if reason == "out_of_stock":
            stats["skipped_out_of_stock"] += amount


def mark_blocked(state: dict, source: str, reason: str, state_path: Path) -> None:
    with _STATE_LOCK:
        state.setdefault("blocked", {})[source] = reason
        save_state(state_path, state)


def reconcile_counts(
    state: dict, pending: dict[str, list[dict]]
) -> None:
    with _STATE_LOCK:
        if state.get("counter_version", 1) < 2:
            for stats in state.get("counts", {}).values():
                out_of_stock = int(stats.get("skipped_out_of_stock", 0))
                stats["scraped"] = int(stats.get("scraped", 0)) + out_of_stock
                stats["skipped_by_reason"] = (
                    {"out_of_stock": out_of_stock} if out_of_stock else {}
                )
        sources = set(state.get("counts", {})) | set(pending)
        for source in sources:
            count_for(state, source)["pending"] = len(pending.get(source, []))
        state["counter_version"] = 2


def mark_done(state: dict, key: str, state_path: Path) -> None:
    with _STATE_LOCK:
        state.setdefault("done", {})[key] = True
        save_state(state_path, state)


def _source_path(root: Path, source: str) -> Path:
    return root / f"{re.sub(r'[^a-z0-9.-]+', '_', source.lower())}.jsonl"


def _file_records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows


def load_seen_and_pending(root: Path, known_urls: set[str]) -> tuple[set[str], dict[str, list[dict]]]:
    seen: set[str] = set()
    pending: dict[str, list[dict]] = defaultdict(list)
    for path in root.glob("*.jsonl"):
        source = path.stem
        for row in _file_records(path):
            url = row.get("url")
            if not url:
                continue
            if url in seen:
                continue
            seen.add(url)
            if url not in known_urls:
                pending[source].append(row)
    return seen, pending


def append_record(
    root: Path, source: str, row: dict, seen: set[str], pending: dict[str, list[dict]],
    state: dict, state_path: Path,
) -> bool:
    with _STATE_LOCK:
        url = row.get("url")
        if not url or url in seen:
            return False
        path = _source_path(root, source)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        seen.add(url)
        pending.setdefault(source, []).append(row)
        count_for(state, source)["pending"] += 1
        save_state(state_path, state)
        return True


def import_pending(
    root: Path, source: str, pending: dict[str, list[dict]], known_urls: set[str],
    state: dict, state_path: Path, chunk_size: int, tag_streak: int,
    max_new_items: int | None = None,
) -> int:
    from app.supabase_import import run_insert_only_file_import

    with _IMPORT_LOCK:
        if state.get("imports_paused") or not pending.get(source):
            return int(state.get("tag_failure_streak", tag_streak))
        records = pending[source][:chunk_size]
        if max_new_items is not None:
            records = records[:max_new_items]
        if not records:
            return int(state.get("tag_failure_streak", tag_streak))
        chunk_root = root / "chunks"
        chunk_root.mkdir(parents=True, exist_ok=True)
        chunk_path = chunk_root / (
            f"{Path(_source_path(root, source)).stem}.{time.time_ns()}.jsonl"
        )
        with chunk_path.open("w", encoding="utf-8") as stream:
            for row in records:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        connection = _ACTIVE_CONNECTION
        if connection is None:
            raise RuntimeError("database connection is not initialized")
        current_streak = int(state.get("tag_failure_streak", tag_streak))
        result = run_insert_only_file_import(
            [chunk_path], connection, known_urls, tag_failure_streak=current_streak,
        )
        with _STATE_LOCK:
            stats = count_for(state, source)
            stats["imported"] += result["inserted"]
            stats["tag_errors"] += result["tag_failed"]
            stats["failed"] += result["failed"]
            add_skip(state, source, "out_of_stock", result["skipped_out_of_stock"])
            duplicate_skips = result["skipped"] - result["skipped_out_of_stock"]
            add_skip(state, source, "already_exists", duplicate_skips)
            stats["pending"] = max(
                0,
                stats["pending"] - result["inserted"] - result["skipped"]
                - result["failed"],
            )
            state["tag_failure_streak"] = result["tag_failure_streak"]
            pending[source] = pending[source][len(records):]
            if result["imports_paused"]:
                state["imports_paused"] = True
                state["pause_reason"] = result["pause_reason"]
                LOG.error(
                    "IMPORTS PAUSED after repeated tag failures: %s",
                    result["pause_reason"],
                )
            LOG.info(
                "import source=%s inserted=%s tagged=%s skipped=%s "
                "tag_errors=%s failed=%s pending=%s",
                source, result["inserted"], result["tagged"], result["skipped"],
                result["tag_failed"], result["failed"], stats["pending"],
            )
            save_state(state_path, state)
        try:
            chunk_path.unlink()
        except OSError:
            pass
        return result["tag_failure_streak"]


def glossary_queries() -> list[tuple[str, str]]:
    data = json.loads(GLOSSARY_PATH.read_text(encoding="utf-8"))
    entries = data if isinstance(data, list) else data.get("styles", [])
    queries: list[tuple[str, str]] = []
    seen: set[str] = set()
    for entry in entries:
        gender = str(entry.get("gender") or "unisex").lower()
        gender = "mens" if "men" in gender or gender in {"m", "male"} else (
            "womens" if "women" in gender or gender in {"w", "female"} else "unisex"
        )
        for query in entry.get("queries", []):
            if isinstance(query, str) and query.strip() and query.casefold() not in seen:
                queries.append((query.strip(), gender))
                seen.add(query.casefold())
    for query in VINTED_CATEGORIES:
        if query.casefold() not in seen:
            queries.append((query, "unisex"))
            seen.add(query.casefold())
    return queries


def _raw_item(item) -> dict:
    return asdict(item) if is_dataclass(item) else dict(item)


def _vinted_unavailable(raw: dict) -> bool:
    for key in ("is_closed", "is_reserved", "is_sold", "is_unavailable"):
        if raw.get(key) is True:
            return True
    if raw.get("is_visible") is False:
        return True
    values = [raw.get("status"), raw.get("status_title"), raw.get("availability")]
    status = " ".join(str(value) for value in values if value is not None)
    return bool(STOCK_WORDS.search(status))


def _row(record: dict, currency: str = "GBP") -> dict:
    return {
        "url": record["source_url"],
        "source": record["source"],
        "title": record["title"],
        "brand": record.get("brand"),
        "price_gbp": record["price_gbp"],
        "currency": currency,
        "gender": record.get("gender_hint"),
        "category": None,
        "condition": record.get("condition_hint"),
        "image_url_original": record["image_url"],
        "record": record,
    }


def _wait_for_vinted_request(last_search_at: float) -> None:
    time.sleep(max(0.0, 1 - (time.monotonic() - last_search_at)))


def scrape_vinted(
    client: PoliteClient, root: Path, state: dict, state_path: Path,
    seen: set[str], pending: dict[str, list[dict]], smoke: bool,
    page_limit: int, chunk_size: int, known_urls: set[str], budget: int,
    tag_streak: int,
) -> int:
    from app.sources import STOCK_PHRASE
    from app.sources.vinted import normalize_item
    from vinted_scraper import VintedScraper

    if state.get("blocked", {}).get("vinted"):
        return tag_streak
    scraper = VintedScraper("https://www.vinted.co.uk", user_agent=USER_AGENT)
    scraper._last_search_at = time.monotonic()
    queries = glossary_queries()[:1] if smoke else glossary_queries()
    blocked_errors = 0
    for query, gender in queries:
        for page in range(1, min(page_limit, 2) + 1 if smoke else page_limit + 1):
            key = f"vinted:{query}:{page}"
            if state.get("done", {}).get(key):
                continue
            if count_for(state, "vinted")["imported"] >= budget and not smoke:
                break
            _wait_for_vinted_request(getattr(scraper, "_last_search_at", 0))
            try:
                items = scraper.search({"search_text": query, "page": page, "per_page": 20})
            except (RuntimeError, ValueError, httpx.HTTPError) as exc:
                LOG.warning("vinted query failed query=%r page=%d error=%s", query, page, exc)
                response = getattr(exc, "response", None)
                status = getattr(response, "status_code", None)
                if status in {403, 429}:
                    blocked_errors += 1
                    if blocked_errors >= 2:
                        mark_blocked(state, "vinted", str(status), state_path)
                        return tag_streak
                continue
            finally:
                scraper._last_search_at = time.monotonic()
            blocked_errors = 0
            if not items:
                mark_done(state, key, state_path)
                break
            for item in items:
                raw = _raw_item(item)
                add_candidate(state, "vinted")
                if (
                    _vinted_unavailable(raw)
                    or STOCK_PHRASE.fullmatch(str(raw.get("title") or "").strip())
                ):
                    add_skip(state, "vinted", "out_of_stock")
                    continue
                currency = str(raw.get("currency") or "").upper()
                if currency not in {"GBP", "£"}:
                    add_skip(state, "vinted", "non_gbp")
                    continue
                record = normalize_item(item, query, gender)
                if not record:
                    add_skip(state, "vinted", "invalid_listing")
                    continue
                if not record.get("image_url") or not record["image_url"].startswith(
                    ("http://", "https://")
                ):
                    add_skip(state, "vinted", "missing_image")
                    continue
                record["availability"] = "https://schema.org/InStock"
                record["raw_json"] = raw
                row = _row(record)
                if not append_record(
                    root, "vinted", row, seen, pending, state, state_path
                ):
                    add_skip(state, "vinted", "already_exists")
            mark_done(state, key, state_path)
            tag_streak = import_pending(
                root, "vinted", pending, known_urls, state, state_path,
                chunk_size, tag_streak,
                max_new_items=10 - count_for(state, "vinted")["imported"] if smoke else
                max(0, budget - count_for(state, "vinted")["imported"]),
            ) if (smoke and count_for(state, "vinted")["imported"] < 10) or (
                len(pending["vinted"]) >= chunk_size
            ) else tag_streak
            LOG.info(
                "progress source=vinted query=%r page=%d scraped=%d imported=%d pending=%d",
                query, page, count_for(state, "vinted")["scraped"],
                count_for(state, "vinted")["imported"],
                count_for(state, "vinted")["pending"],
            )
    return tag_streak


def _xml_locs(content: bytes) -> tuple[list[str], bool]:
    try:
        if content[:2] == b"\x1f\x8b":
            content = gzip.decompress(content)
        root = ET.fromstring(content)
        locs = [
            node.text.strip()
            for node in root.iter()
            if node.tag.rsplit("}", 1)[-1] == "loc" and node.text
        ]
        return locs, any(node.tag.rsplit("}", 1)[-1] == "sitemap" for node in root)
    except (ET.ParseError, OSError):
        return [], False


def sitemap_product_urls(client: PoliteClient, base: str) -> list[str]:
    try:
        robots = client.get(f"{base}/robots.txt", interval=1)
        robots_text = robots.text if robots.status_code < 400 else ""
    except httpx.HTTPError as exc:
        LOG.warning("robots.txt fetch failed base=%s error=%s", base, exc)
        robots_text = ""
    sitemap_urls = re.findall(r"(?im)^sitemap:\s*(\S+)", robots_text) or [
        f"{base}/sitemap.xml"
    ]
    uk_sitemaps = [
        sitemap for sitemap in sitemap_urls
        if re.search(r"/(?:uk|en-gb)(?:/|$)", urlsplit(sitemap).path, re.IGNORECASE)
    ]
    if uk_sitemaps:
        sitemap_urls = uk_sitemaps
    products: list[str] = []
    visited: set[str] = set()
    queue = list(sitemap_urls)
    while queue and len(visited) < 30:
        sitemap = queue.pop(0)
        if sitemap in visited:
            continue
        visited.add(sitemap)
        try:
            response = client.get(sitemap, interval=1)
            if response.status_code >= 400:
                continue
            locs, is_index = _xml_locs(response.content)
        except httpx.HTTPError as exc:
            LOG.warning("sitemap fetch failed url=%s error=%s", sitemap, exc)
            continue
        if is_index:
            country_indexes = [
                loc for loc in locs
                if re.search(
                    r"/(?:uk|en-gb)/sitemap_index\.xml$",
                    urlsplit(loc).path,
                    re.IGNORECASE,
                )
            ]
            if country_indexes:
                queue.extend(country_indexes)
            else:
                child_sitemaps = [
                    loc for loc in locs
                    if (
                        "product" in urlsplit(loc).path.lower()
                        or re.search(
                            r"/sitemap_0\.xml$", urlsplit(loc).path, re.IGNORECASE
                        )
                    )
                    and "refinedcategories" not in urlsplit(loc).path.lower()
                ]
                queue.extend(child_sitemaps or locs)
        else:
            if re.search(r"/sitemap_0\.xml$", urlsplit(sitemap).path, re.IGNORECASE):
                products.extend(locs)
            else:
                products.extend(
                    loc for loc in locs
                    if re.search(
                        r"/(product|products|p)/", urlsplit(loc).path, re.IGNORECASE
                    )
                )
    return list(dict.fromkeys(products))


def _jsonld_products(value):
    if isinstance(value, list):
        for entry in value:
            yield from _jsonld_products(entry)
    elif isinstance(value, dict):
        kind = value.get("@type")
        kinds = kind if isinstance(kind, list) else [kind]
        if any(
            str(entry).rstrip("/").rsplit("/", 1)[-1] == "Product"
            for entry in kinds
        ):
            yield value
        for key in ("@graph", "mainEntity", "itemListElement"):
            if key in value:
                yield from _jsonld_products(value[key])


def parse_product_page(
    html: str, page_url: str, source: str
) -> tuple[dict | None, str | None]:
    from html.parser import HTMLParser

    from app.sources import STOCK_PHRASE
    from app.sources.listing_readers import infer_category

    class Scripts(HTMLParser):
        def __init__(self):
            super().__init__()
            self.active = False
            self.data: list[str] = []
            self.scripts: list[str] = []

        def handle_starttag(self, tag, attrs):
            if tag.lower() == "script" and dict(attrs).get("type", "").lower() == "application/ld+json":
                self.active = True
                self.data = []

        def handle_endtag(self, tag):
            if tag.lower() == "script" and self.active:
                self.scripts.append("".join(self.data))
                self.active = False

        def handle_data(self, data):
            if self.active:
                self.data.append(data)

    parser = Scripts()
    parser.feed(html)
    reason = None
    for script in parser.scripts:
        try:
            payload = json.loads(script)
        except json.JSONDecodeError:
            continue
        for product in _jsonld_products(payload):
            title = str(product.get("name") or "").strip()
            if not title:
                reason = reason or "missing_title"
                continue
            if STOCK_PHRASE.fullmatch(title):
                reason = "out_of_stock"
                continue
            offer_values = product.get("offers") or []
            offers = offer_values if isinstance(offer_values, list) else [offer_values]
            expanded_offers = []
            for candidate in offers:
                if not isinstance(candidate, dict):
                    continue
                nested = candidate.get("offers")
                expanded_offers.extend(
                    nested if isinstance(nested, list) else [nested or candidate]
                )
            offers = expanded_offers
            in_stock_offers = [
                candidate for candidate in offers
                if isinstance(candidate, dict)
                and urlsplit(
                    str(candidate.get("availability") or "")
                ).path.rstrip("/").split("/")[-1].lower() == "instock"
            ]
            offer = next(
                (
                    candidate for candidate in in_stock_offers
                    if isinstance(candidate, dict)
                    and str(candidate.get("priceCurrency", "")).upper() == "GBP"
                ),
                None,
            )
            if not offer:
                reason = "non_gbp" if in_stock_offers else (
                    "out_of_stock" if offers else "missing_offer"
                )
                continue
            try:
                price = float(offer.get("price") or offer.get("lowPrice"))
            except (TypeError, ValueError):
                reason = "invalid_price"
                continue
            if price <= 0:
                reason = "invalid_price"
                continue
            images = product.get("image") or []
            if isinstance(images, (str, dict)):
                images = [images]
            image_candidates = [
                entry.get("url") or entry.get("contentUrl") if isinstance(entry, dict) else entry
                for entry in images
            ]
            image = next(
                (
                    urljoin(page_url, value)
                    for value in image_candidates
                    if isinstance(value, str) and value.startswith(("http", "/"))
                ),
                None,
            )
            if not image:
                reason = "missing_image"
                continue
            category_text = " ".join(
                str(value) for value in (
                    product.get("category"), product.get("productType"), product.get("name"),
                    urlsplit(page_url).path,
                ) if value
            )
            if NON_APPAREL_WORDS.search(category_text):
                reason = reason or "non_apparel"
                continue
            category = infer_category(category_text)
            if not APPAREL_WORDS.search(category_text) and category in {None, "other"}:
                reason = reason or "non_apparel"
                continue
            canonical = str(product.get("url") or offer.get("url") or page_url)
            url = urljoin(page_url, canonical)
            if urlsplit(url).hostname != urlsplit(page_url).hostname:
                url = page_url
            brand = product.get("brand")
            if isinstance(brand, dict):
                brand = brand.get("name")
            record = {
                "id": f"{source}:{hashlib.sha1(url.encode()).hexdigest()[:20]}",
                "source": source, "source_url": url, "title": title,
                "brand": str(brand) if brand else None, "price_gbp": price,
                "size": None, "description": product.get("description"),
                "image_url": image, "gender_hint": None, "condition_hint": "new",
                "availability": str(offer.get("availability")),
                "query": None, "raw_json": product,
            }
            return record, None
    return None, reason


def scrape_sitemaps(
    client, root, state, state_path, seen, pending, smoke, chunk_size,
    known_urls, budgets, tag_streak,
) -> int:
    for source, base in SITEMAP_SHOPS.items():
        urls = sitemap_product_urls(client, base)
        if smoke:
            urls = urls[:20]
        visited = 0
        for url in urls:
            key = f"sitemap:{source}:{url}"
            if state.get("done", {}).get(key):
                continue
            if not smoke and count_for(state, source)["imported"] >= budgets[source]:
                break
            try:
                response = client.get(url, interval=1)
                if response.status_code in {403, 429}:
                    LOG.warning("sitemap host blocked source=%s status=%s", source, response.status_code)
                    mark_blocked(state, source, str(response.status_code), state_path)
                    break
                if response.status_code >= 400:
                    add_candidate(state, source)
                    add_skip(state, source, "http_error")
                    mark_done(state, key, state_path)
                    continue
                add_candidate(state, source)
                record, reason = parse_product_page(response.text, str(response.url), source)
                if record:
                    if not append_record(
                        root, source, _row(record), seen, pending, state, state_path
                    ):
                        add_skip(state, source, "already_exists")
                else:
                    add_skip(state, source, reason or "no_product_jsonld")
            except (httpx.HTTPError, ValueError) as exc:
                LOG.warning("product page failed source=%s url=%s error=%s", source, url, exc)
                continue
            mark_done(state, key, state_path)
            visited += 1
            if smoke and visited >= 20:
                break
            if smoke and count_for(state, source)["imported"] < 10 and len(pending[source]) >= 10:
                tag_streak = import_pending(
                    root, source, pending, known_urls, state, state_path, chunk_size,
                    tag_streak, max_new_items=10 - count_for(state, source)["imported"],
                )
            elif len(pending[source]) >= chunk_size:
                tag_streak = import_pending(
                    root, source, pending, known_urls, state, state_path,
                    chunk_size, tag_streak,
                    max_new_items=max(0, budgets[source] - count_for(state, source)["imported"]),
                )
        LOG.info(
            "progress source=%s visited=%d scraped=%d imported=%d pending=%d",
            source, visited, count_for(state, source)["scraped"],
            count_for(state, source)["imported"], count_for(state, source)["pending"],
        )
    return tag_streak


def _shopify_products(content: bytes) -> list[dict]:
    try:
        payload = json.loads(content)
        return payload.get("products", [])
    except (json.JSONDecodeError, AttributeError):
        return []


def _variant_color(product: dict, variant: dict) -> str:
    options = {}
    for index, option in enumerate(product.get("options") or []):
        if not isinstance(option, dict):
            continue
        try:
            position = int(option.get("position", index + 1))
        except (TypeError, ValueError):
            position = index + 1
        options[position] = str(option.get("name", "")).lower()
    for position, name in options.items():
        if name in {"color", "colour"}:
            value = variant.get(f"option{position}")
            if value:
                return str(value).strip()
            values = variant.get("options") or []
            if len(values) >= position and values[position - 1]:
                return str(values[position - 1]).strip()
    return "default"


def _shopify_rows(
    host: str, products: list[dict], currency: str
) -> tuple[list[dict], dict[str, int]]:
    from app.sources import STOCK_PHRASE

    rows = []
    skipped: dict[str, int] = defaultdict(int)
    for product in products:
        title = str(product.get("title") or "").strip()
        if not title:
            skipped["missing_title"] += 1
            continue
        if STOCK_PHRASE.fullmatch(title):
            skipped["out_of_stock"] += 1
            continue
        grouped: dict[str, list[dict]] = defaultdict(list)
        for variant in product.get("variants") or []:
            if variant.get("available") is True:
                grouped[_variant_color(product, variant)].append(variant)
        if not grouped:
            skipped["out_of_stock"] += 1
            continue
        images = product.get("images") or []
        primary = product.get("image")
        if isinstance(primary, dict):
            primary = primary.get("src")
        if not primary and images:
            first = images[0]
            primary = first.get("src") if isinstance(first, dict) else first
        if not isinstance(primary, str) or not primary.startswith(("http://", "https://")):
            skipped["missing_image"] += len(grouped)
            continue
        for color, variants in grouped.items():
            variant = variants[0]
            try:
                price = float(variant.get("price"))
            except (TypeError, ValueError):
                skipped["invalid_price"] += 1
                continue
            if price <= 0:
                skipped["invalid_price"] += 1
                continue
            handle = product.get("handle")
            if not handle:
                skipped["missing_handle"] += 1
                continue
            url = f"https://{host}/products/{handle}?variant={variant.get('id')}"
            display_title = f"{title} - {color}" if color != "default" else title
            record = {
                "id": f"shopify:{hashlib.sha1(url.encode()).hexdigest()}",
                "source": host, "source_url": url, "title": display_title,
                "brand": product.get("vendor"), "price_gbp": price,
                "size": None, "description": product.get("body_html"),
                "image_url": primary, "gender_hint": None, "condition_hint": "new",
                "availability": "https://schema.org/InStock", "query": None,
                "raw_json": {"product": product, "color": color, "available_variants": variants},
            }
            rows.append(_row(record, currency))
    return rows, dict(skipped)


def _shopify_get(
    client: PoliteClient, url: str, backoff: int, *, smoke: bool = False
) -> httpx.Response | None:
    for retry in range(4):
        try:
            response = client.get(
                url, interval=10, scope=urlsplit(url).hostname
            )
        except httpx.HTTPError as exc:
            LOG.warning("shopify request failed url=%s error=%s", url, exc)
            return None
        if response.status_code != 429:
            return response
        if smoke:
            LOG.warning("shopify smoke blocked on first 429: %s", urlsplit(url).hostname)
            return response
        if retry == 3:
            LOG.error("shopify store skipped after four 429s: %s", urlsplit(url).hostname)
            return None
        LOG.warning("shopify 429 host=%s retry=%d backoff=%ds", urlsplit(url).hostname, retry + 1, backoff)
        time.sleep(backoff)
    return None


def scrape_shopify(
    client, root, state, state_path, seen, pending, smoke, chunk_size,
    known_urls, budgets, tag_streak, backoff: int, max_pages: int,
) -> int:
    for host in SHOPIFY_STORES:
        if not smoke and count_for(state, host)["imported"] >= budgets[host]:
            continue
        if state.get("blocked", {}).get(host):
            continue
        cart = _shopify_get(
            client, f"https://{host}/cart.js", backoff, smoke=smoke
        )
        if cart is None or cart.status_code >= 400:
            LOG.warning("shopify currency unavailable host=%s", host)
            mark_blocked(
                state, host,
                "network-error" if cart is None else str(cart.status_code),
                state_path,
            )
            continue
        try:
            currency = str(cart.json().get("currency") or "").upper()
        except (json.JSONDecodeError, AttributeError):
            currency = ""
        if currency != "GBP":
            LOG.warning("shopify skipped non-GBP store host=%s currency=%s", host, currency)
            continue
        page_limit = 1 if smoke else max_pages
        for page in range(1, page_limit + 1):
            key = f"shopify:{host}:page:{page}"
            if state.get("done", {}).get(key):
                continue
            if not smoke and count_for(state, host)["imported"] >= budgets[host]:
                break
            response = _shopify_get(
                client, f"https://{host}/products.json?limit=250&page={page}",
                backoff, smoke=smoke,
            )
            if response is None:
                mark_blocked(state, host, "network-error-or-retries-exhausted", state_path)
                break
            if response.status_code in {403, 404, 429}:
                LOG.warning("shopify feed unavailable host=%s status=%s", host, response.status_code)
                mark_blocked(state, host, str(response.status_code), state_path)
                break
            products = _shopify_products(response.content)
            if response.status_code >= 400:
                mark_blocked(state, host, str(response.status_code), state_path)
                break
            if not products:
                mark_done(state, key, state_path)
                break
            rows, skipped = _shopify_rows(host, products, currency)
            add_candidate(state, host, len(rows) + sum(skipped.values()))
            for reason, count in skipped.items():
                add_skip(state, host, reason, count)
            for row in rows:
                if not append_record(root, host, row, seen, pending, state, state_path):
                    add_skip(state, host, "already_exists")
            mark_done(state, key, state_path)
            if smoke and len(pending[host]) >= 10:
                tag_streak = import_pending(
                    root, host, pending, known_urls, state, state_path,
                    chunk_size, tag_streak, max_new_items=10 - count_for(state, host)["imported"],
                )
            elif len(pending[host]) >= chunk_size:
                tag_streak = import_pending(
                    root, host, pending, known_urls, state, state_path,
                    chunk_size, tag_streak,
                    max_new_items=max(0, budgets[host] - count_for(state, host)["imported"]),
                )
            LOG.info(
                "progress source=%s page=%d scraped=%d imported=%d pending=%d",
                host, page, count_for(state, host)["scraped"],
                count_for(state, host)["imported"], count_for(state, host)["pending"],
            )
    return tag_streak


def connect_database():
    import psycopg

    dsn = os.environ.get("QUENYA_DATABASE_URL")
    if not dsn:
        raise RuntimeError("QUENYA_DATABASE_URL is required")
    connection = psycopg.connect(dsn, prepare_threshold=None)
    with connection.cursor() as cursor:
        cursor.execute("SELECT source_url FROM quenya.items")
        known = {row[0] for row in cursor.fetchall()}
    LOG.info("loaded existing item URLs count=%d", len(known))
    return connection, known


def run_source_families(jobs: dict[str, object]) -> dict[str, str]:
    errors = {}
    with ThreadPoolExecutor(max_workers=len(jobs)) as executor:
        futures = {
            executor.submit(job): source for source, job in jobs.items()
        }
        for future in as_completed(futures):
            source = futures[future]
            try:
                future.result()
            except Exception as exc:
                errors[source] = f"{type(exc).__name__}: {exc}"
                LOG.exception("source family failed: %s", source)
    return errors


def run_job(args) -> dict:
    load_runtime_env()
    root = OUT_ROOT / "smoke" if args.smoke else OUT_ROOT
    root.mkdir(parents=True, exist_ok=True)
    state_path = root / "state.json"
    state = read_state(state_path)
    connection, known_urls = connect_database()
    global _ACTIVE_CONNECTION
    _ACTIVE_CONNECTION = connection
    seen, pending = load_seen_and_pending(root, known_urls)
    seen.update(known_urls)
    reconcile_counts(state, pending)
    save_state(state_path, state)
    if state.get("imports_paused"):
        LOG.error("imports remain paused: %s", state.get("pause_reason"))
    sources = ["vinted", *SITEMAP_SHOPS, *SHOPIFY_STORES]
    if args.smoke:
        budgets = {source: 10 for source in sources}
    else:
        base_budget, extra = divmod(args.max_imported, len(sources))
        budgets = {
            source: base_budget + (index < extra)
            for index, source in enumerate(sources)
        }
    tag_streak = int(state.get("tag_failure_streak", 0))

    def run_family(scraper):
        client = PoliteClient()
        try:
            return scraper(client)
        finally:
            client.close()

    try:
        if pending and not state.get("imports_paused"):
            for source in sources:
                if len(pending.get(source, [])) >= (10 if args.smoke else args.chunk_size):
                    tag_streak = import_pending(
                        root, source, pending, known_urls, state, state_path,
                        args.chunk_size, tag_streak,
                        max_new_items=max(0, budgets[source] - count_for(state, source)["imported"]),
                    )
        family_jobs = {
            "vinted": lambda: run_family(
                lambda client: scrape_vinted(
                    client, root, state, state_path, seen, pending, args.smoke,
                    args.vinted_pages, args.chunk_size, known_urls,
                    budgets["vinted"], tag_streak,
                ),
            ),
            "sitemaps": lambda: run_family(
                lambda client: scrape_sitemaps(
                    client, root, state, state_path, seen, pending, args.smoke,
                    args.chunk_size, known_urls, budgets, tag_streak,
                ),
            ),
            "shopify": lambda: run_family(
                lambda client: scrape_shopify(
                    client, root, state, state_path, seen, pending, args.smoke,
                    args.chunk_size, known_urls, budgets, tag_streak,
                    args.shopify_backoff, args.shopify_pages,
                ),
            ),
        }
        family_errors = run_source_families(family_jobs)
        tag_streak = int(state.get("tag_failure_streak", tag_streak))
        if family_errors:
            with _STATE_LOCK:
                state.setdefault("family_errors", {}).update(family_errors)
                save_state(state_path, state)
        if not state.get("imports_paused"):
            for source in sources:
                remaining = max(0, budgets[source] - count_for(state, source)["imported"])
                if pending.get(source) and remaining:
                    tag_streak = import_pending(
                        root, source, pending, known_urls, state, state_path,
                        args.chunk_size, tag_streak, max_new_items=remaining,
                    )
        state["tag_failure_streak"] = tag_streak
        save_state(state_path, state)
        LOG.info(
            "job complete-for-now smoke=%s counts=%s",
            args.smoke, state.get("counts"),
        )
        return state
    finally:
        connection.close()
        _ACTIVE_CONNECTION = None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--max-imported", type=int, default=10_000)
    parser.add_argument("--chunk-size", type=int, default=200)
    parser.add_argument("--vinted-pages", type=int, default=10)
    parser.add_argument("--shopify-pages", type=int, default=40)
    parser.add_argument("--shopify-backoff", type=int, default=600)
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )
    run_job(args)


if __name__ == "__main__":
    main()
