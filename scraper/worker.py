import argparse
import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

import httpx
import psycopg
from PIL import Image, ImageOps
from psycopg.types.json import Jsonb
from scrapling.fetchers import StealthyFetcher

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.sources.listing_readers import infer_category, parse_listing

ROOT = Path(__file__).resolve().parent
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)
CATEGORY_PAGES = {
    "cos.com": [
        ("m", "https://www.cos.com/en-gb/men/knitwear"),
        ("m", "https://www.cos.com/en-gb/men/shirts"),
        ("m", "https://www.cos.com/en-gb/men/trousers"),
        ("m", "https://www.cos.com/en-gb/men/jackets-and-coats"),
        ("w", "https://www.cos.com/en-gb/women/knitwear"),
        ("w", "https://www.cos.com/en-gb/women/trousers"),
        ("w", "https://www.cos.com/en-gb/women/tops"),
        ("w", "https://www.cos.com/en-gb/women/dresses"),
    ],
    "arket.com": [
        ("m", "https://www.arket.com/en-gb/men/clothing/knitwear/"),
        ("m", "https://www.arket.com/en-gb/men/clothing/trousers/"),
        ("m", "https://www.arket.com/en-gb/men/clothing/jackets-coats/"),
        ("m", "https://www.arket.com/en-gb/men/clothing/shirts/"),
        ("w", "https://www.arket.com/en-gb/women/clothing/knitwear/"),
        ("w", "https://www.arket.com/en-gb/women/clothing/trousers/"),
        ("w", "https://www.arket.com/en-gb/women/clothing/jackets-coats/"),
        ("w", "https://www.arket.com/en-gb/women/clothing/dresses/"),
    ],
    "stories.com": [
        ("w", "https://www.stories.com/en-gb/clothing/knitwear/"),
        ("w", "https://www.stories.com/en-gb/clothing/trousers/"),
        ("w", "https://www.stories.com/en-gb/clothing/jackets-coats/"),
        ("w", "https://www.stories.com/en-gb/clothing/dresses/"),
    ],
    "barbour.com": [
        ("m", "https://www.barbour.com/gb/mens/clothing/knitwear"),
        ("m", "https://www.barbour.com/gb/mens/clothing/shirts"),
        ("m", "https://www.barbour.com/gb/mens/clothing/jackets"),
        ("m", "https://www.barbour.com/gb/mens/clothing/trousers"),
        ("w", "https://www.barbour.com/gb/womens/clothing/knitwear"),
        ("w", "https://www.barbour.com/gb/womens/clothing/shirts"),
        ("w", "https://www.barbour.com/gb/womens/clothing/jackets"),
        ("w", "https://www.barbour.com/gb/womens/clothing/dresses"),
    ],
    "johnsmedley.com": [
        ("m", "https://www.johnsmedley.com/uk/mens/shop-by-style"),
        ("m", "https://www.johnsmedley.com/uk/mens/knitwear"),
        ("m", "https://www.johnsmedley.com/uk/mens/polo-shirts"),
        ("w", "https://www.johnsmedley.com/uk/womens/view-all-women"),
        ("w", "https://www.johnsmedley.com/uk/womens/knitwear"),
        ("w", "https://www.johnsmedley.com/uk/womens/dresses"),
    ],
    "charlestyrwhitt.com": [
        ("m", "https://www.charlestyrwhitt.com/uk/mens-trousers/"),
        ("m", "https://www.charlestyrwhitt.com/uk/mens-shirts/"),
        ("m", "https://www.charlestyrwhitt.com/uk/mens-jumpers/"),
        ("m", "https://www.charlestyrwhitt.com/uk/mens-suits/"),
    ],
    "asos.com": [
        ("m", "https://www.asos.com/men/jumpers-cardigans/cat/?cid=7617"),
        ("m", "https://www.asos.com/men/shirts/cat/?cid=3602"),
        ("m", "https://www.asos.com/men/trousers-chinos/cat/?cid=4910"),
        ("m", "https://www.asos.com/men/coats-jackets/cat/?cid=3606"),
        ("w", "https://www.asos.com/women/trousers-leggings/cat/?cid=2640"),
        ("w", "https://www.asos.com/women/dresses/cat/?cid=8799"),
        ("w", "https://www.asos.com/women/coats-jackets/cat/?cid=2641"),
        ("w", "https://www.asos.com/women/tops/cat/?cid=4169"),
    ],
    "mango.com": [
        ("w", "https://shop.mango.com/gb/en/c/women/trousers/0bf28b3b"),
        ("w", "https://shop.mango.com/gb/en/c/women/jeans_0bf28b3b"),
        ("w", "https://shop.mango.com/gb/en/c/women/dresses-and-jumpsuits_d0c8fdc1"),
        ("w", "https://shop.mango.com/gb/en/c/women/jackets_0d4b5eb2"),
    ],
}


def _env():
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _scroll_listing(page):
    for _ in range(4):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(350)


def _fetch(url, timeout_seconds=45):
    last_error = None
    deadline = time.monotonic() + max(1, timeout_seconds)
    for attempt in range(2):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            page = StealthyFetcher.fetch(
                url, headless=True, load_dom=True, wait=3000,
                page_action=_scroll_listing, timeout=max(1000, int(remaining * 1000)),
                locale="en-GB",
                timezone_id="Europe/London",
            )
            body = getattr(page, "body", b"")
            html = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body)
            if int(getattr(page, "status", 0) or 0) == 200 and len(html) > 500:
                return html, str(getattr(page, "url", url) or url)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        if attempt == 0 and time.monotonic() < deadline:
            time.sleep(min(5, max(0, deadline - time.monotonic())))
    if last_error:
        raise last_error
    return "", url


def _jpeg(data: bytes) -> bytes:
    with Image.open(BytesIO(data)) as original:
        image = ImageOps.exif_transpose(original).convert("RGB")
        output = BytesIO()
        image.save(output, format="JPEG", quality=88, optimize=True)
    return output.getvalue()


def _upload_image(client, record, shop, supabase_url, secret):
    original = record["image_url"]
    response = client.get(original, headers={"User-Agent": UA, "Referer": record["source_url"]})
    response.raise_for_status()
    content = _jpeg(response.content)
    key = hashlib.sha1(record["source_url"].encode()).hexdigest()[:16]
    path = f"{shop}/{key}.jpg"
    headers = {
        "Authorization": f"Bearer {secret}", "apikey": secret,
        "x-upsert": "true", "Content-Type": "image/jpeg",
    }
    result = client.post(
        f"{supabase_url.rstrip('/')}/storage/v1/object/quenya-listings/{quote(path, safe='/')}",
        headers=headers, content=content,
    )
    result.raise_for_status()
    return path


def _ensure_bucket(client, supabase_url, secret):
    headers = {"Authorization": f"Bearer {secret}", "apikey": secret}
    response = client.post(
        f"{supabase_url.rstrip('/')}/storage/v1/bucket",
        headers={**headers, "Content-Type": "application/json"},
        json={"id": "quenya-listings", "name": "quenya-listings", "public": False},
    )
    if response.status_code not in {200, 201, 409}:
        response.raise_for_status()


def _category(title, url):
    return infer_category(title, url)


def run(args):
    _env()
    deadline = time.monotonic() + max(0, args.time_budget) * 60
    selected = set(filter(None, (args.shops or "").split(",")))
    pages = {
        shop: entries[:args.max_pages] if args.max_pages else entries
        for shop, entries in CATEGORY_PAGES.items()
        if not selected or shop in selected
    }
    out = ROOT / "out" / f"listings-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    secret = os.getenv("SUPABASE_SECRET_KEY", "")
    db_url = os.getenv("SUPABASE_DB_URL", "")
    counts = {shop: {"fetched": 0, "inserted": 0, "skipped": 0, "errors": 0} for shop in pages}
    with httpx.Client(timeout=30, follow_redirects=True) as client, out.open(
        "w", encoding="utf-8", newline="\n"
    ) as output:
        if not args.dry_run:
            if not supabase_url or not secret or not db_url:
                raise RuntimeError("SUPABASE_URL, SUPABASE_SECRET_KEY and SUPABASE_DB_URL are required")
            _ensure_bucket(client, supabase_url, secret)
        connection = psycopg.connect(db_url) if not args.dry_run else None
        try:
            stop = False
            for shop, categories in pages.items():
                last_request = 0.0
                for gender, url in categories:
                    if time.monotonic() >= deadline:
                        print("Time budget reached; keeping written output.", flush=True)
                        stop = True
                        break
                    wait = 5 - (time.monotonic() - last_request)
                    if last_request and wait > 0:
                        time.sleep(wait)
                    if time.monotonic() >= deadline:
                        print("Time budget reached; keeping written output.", flush=True)
                        stop = True
                        break
                    page_started = time.monotonic()
                    parsed_count = 0
                    try:
                        last_request = time.monotonic()
                        html, final_url = _fetch(
                            url, timeout_seconds=min(45, deadline - page_started)
                        )
                        records = parse_listing(html, final_url, shop, gender)
                        parsed_count = len(records)
                        counts[shop]["fetched"] += len(records)
                        for record in records:
                            row = {
                                "url": record["source_url"], "source": shop,
                                "title": record["title"], "brand": record.get("brand"),
                                "price_gbp": record["price_gbp"], "gender": gender,
                                "category": _category(record.get("title"), url),
                                "condition": "new",
                                "image_url_original": record["image_url"],
                                "record": record,
                            }
                            if args.dry_run:
                                output.write(json.dumps(row, ensure_ascii=False) + "\n")
                                counts[shop]["skipped"] += 1
                                continue
                            try:
                                row["image_path"] = _upload_image(
                                    client, record, shop, supabase_url, secret
                                )
                                record["image_path"] = row["image_path"]
                                with connection.cursor() as cursor:
                                    cursor.execute(
                                        """INSERT INTO quenya.listings
                                           (url, source, title, brand, price_gbp, gender,
                                            category, condition, image_path,
                                            image_url_original, record)
                                           VALUES (%(url)s, %(source)s, %(title)s, %(brand)s,
                                            %(price_gbp)s, %(gender)s, %(category)s,
                                            %(condition)s, %(image_path)s,
                                            %(image_url_original)s, %(record)s)
                                           ON CONFLICT (url) DO NOTHING""",
                                        {**row, "record": Jsonb(record)},
                                    )
                                    inserted = cursor.rowcount
                                connection.commit()
                                counts[shop]["inserted" if inserted else "skipped"] += 1
                            except psycopg.Error:
                                connection.rollback()
                                counts[shop]["errors"] += 1
                    except Exception:  # noqa: BLE001
                        counts[shop]["errors"] += 1
                    finally:
                        output.flush()
                        print(
                            f"{shop} {url} {time.monotonic() - page_started:.1f}s "
                            f"parsed={parsed_count}",
                            flush=True,
                        )
                if stop:
                    break
        finally:
            if connection:
                connection.close()
    print(json.dumps({"output": str(out), "dry_run": args.dry_run, "shops": counts}, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--shops", help="comma-separated shop domains")
    parser.add_argument("--max-pages", type=int, default=0, help="maximum pages per shop")
    parser.add_argument("--time-budget", type=float, default=12, help="overall time budget in minutes")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
