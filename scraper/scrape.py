import argparse
import json
import math
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from tomllib import load as load_toml

import httpx

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "backend"))

from adapters import REGISTRY  # noqa: E402
from adapters.common import Blocked, ShopSession  # noqa: E402
from store import _ensure_bucket, upload_rows  # noqa: E402


def _prepare_fx_rates(shops):
    eur_shops = [
        shop
        for shop in shops
        if shop.get("adapter") == "shopify"
        and str(shop.get("currency") or "GBP").upper() == "EUR"
    ]
    if not eur_shops:
        return
    rate = None
    try:
        response = httpx.get(
            "https://api.frankfurter.app/latest?from=EUR&to=GBP", timeout=10
        )
        response.raise_for_status()
        rate = float(response.json()["rates"]["GBP"])
        if not math.isfinite(rate) or rate <= 0:
            rate = None
    except (httpx.HTTPError, KeyError, TypeError, ValueError, OverflowError):
        rate = None
    for shop in eur_shops:
        shop["_fx_rate"] = rate


def _load_env():
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def load_shops():
    with (ROOT / "shops.toml").open("rb") as stream:
        data = load_toml(stream)
    shops = data.get("shop", [])
    for shop in shops:
        if not shop.get("brand"):
            shop["brand"] = None
    return shops


def _shop_filter(shops, args, probe=False):
    selected = set(filter(None, (args.shops or "").split(",")))
    category_set = getattr(args, "category_set", "apparel")
    result = []
    for shop in shops:
        if selected and shop["key"] not in selected:
            continue
        if args.group and shop.get("group") != args.group:
            continue
        category_sets = shop.get("category_sets")
        if (
            category_set == "apparel"
            and category_sets is not None
            and "apparel" not in category_sets
        ):
            continue
        if category_set == "tees" and "tees" not in (category_sets or []):
            continue
        if category_set == "accessories" and not (
            shop.get("adapter") == "shopify" or shop.get("accessory_pages")
        ):
            continue
        if probe:
            result.append(shop)
            continue
        if not shop.get("enabled"):
            continue
        if shop.get("where") == "uk" and not (args.uk or args.all_enabled or selected):
            continue
        result.append(shop)
    return result


def _list(shops):
    print(f"{'KEY':18} {'BRAND':24} {'ADAPTER':10} {'WHERE':6} {'ENABLED':7} STATUS")
    for shop in shops:
        print(
            f"{shop['key']:18} {str(shop.get('brand') or '—')[:24]:24} "
            f"{shop['adapter']:10} {shop.get('where', 'any'):6} "
            f"{str(shop.get('enabled', False)):7} {shop.get('status', '')}"
        )


def _probe(shops, args):
    category_set = getattr(args, "category_set", "apparel")
    host_locks = {}
    host_locks_lock = threading.Lock()

    def probe_one(original):
        shop = dict(original)
        shop["_category_set"] = category_set
        host = str(shop.get("host") or "").lower()
        with host_locks_lock:
            host_lock = host_locks.setdefault(host, threading.Lock())
        session = ShopSession(
            shop,
            max_requests=2,
            delay=getattr(args, "delay", None),
            jitter=getattr(args, "jitter", None),
            check_robots=False,
            probe=True,
        )
        status, reason, rows = "error", "", []
        try:
            with host_lock:
                rows = REGISTRY[shop["adapter"]](shop, session, probe=True)
            if shop.get("_status"):
                status = shop["_status"]
                reason = shop.get("_reason", "")
            elif session.skipped:
                status, reason = "robots", "listing page disallowed by robots.txt"
            elif any(item.get("status") != 200 for item in session.requests) and not rows:
                status, reason = "error", "One or more listing requests failed"
            elif rows:
                status = "ok"
                label = {"accessories": "accessories", "tees": "tees"}.get(category_set, "clothing")
                reason = f"GBP {label} rows accepted"
            elif session.requests:
                status = "empty"
                label = {"accessories": "accessories", "tees": "tees"}.get(category_set, "clothing")
                reason = f"No accepted GBP {label} rows"
            else:
                status = "error"
                reason = "No request completed"
        except Blocked as exc:
            status, reason = exc.status, str(exc)
        except Exception as exc:  # noqa: BLE001
            status, reason = "error", f"{type(exc).__name__}: {exc}"
        sample = rows[0] if rows else None
        result = {
            "key": shop["key"],
            "brand": shop.get("brand"),
            "adapter": shop["adapter"],
            "status": status,
            "reason": reason,
            "requests": session.requests,
            "rows": len(rows),
            "sample": {
                "title": sample["title"],
                "price_gbp": sample["price_gbp"],
                "url": sample["url"],
                "image": sample["image_url_original"],
            }
            if sample
            else None,
        }
        print(
            f"{shop['key']}: {status} requests={len(session.requests)} rows={len(rows)} {reason}",
            flush=True,
        )
        return result

    results = []
    with ThreadPoolExecutor(max_workers=max(1, getattr(args, "workers", 8))) as pool:
        futures = [pool.submit(probe_one, shop) for shop in shops]
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda item: item["key"])
    out_dir = ROOT / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"probe-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Probe report: {path}")
    return results


def _markdown(summary):
    lines = [
        "# Scrape summary",
        "",
        f"Run: `{summary['run_id']}`",
        f"Total accepted rows: **{summary['total_rows']}**",
        "",
        "| Shop | Status | Requests | Rows | Gender split | Price range | Sample | Reason |",
        "|---|---|---:|---:|---|---:|---|---|",
    ]
    for shop in summary["shops"]:
        split = (
            ", ".join(
                f"{'unknown' if key is None or key == 'null' else key} {value}"
                for key, value in sorted(
                    shop["gender_split"].items(), key=lambda item: str(item[0])
                )
            )
            or "—"
        )
        price = (
            f"£{shop['price_min']:.2f}–£{shop['price_max']:.2f}"
            if shop.get("price_min") is not None
            else "—"
        )
        sample = shop.get("sample") or {}
        sample_text = (
            f"{sample.get('title', '')} £{sample.get('price_gbp', '')} [{sample.get('url', '')}]"
            if sample
            else "—"
        )
        lines.append(
            f"| {shop['key']} | {shop['status']} | {len(shop['requests'])} | {shop['rows']} | "
            f"{split} | {price} | {sample_text} | {shop.get('reason', '')} |"
        )
    lines.extend(["", "## Image checks", ""])
    for shop in summary["shops"]:
        if shop.get("image_checks"):
            lines.append(
                f"- **{shop['key']}**: "
                + ", ".join(str(item["status"]) for item in shop["image_checks"])
            )
    return "\n".join(lines) + "\n"


def split_batches(rows, batch_size):
    batch_size = max(1, batch_size)
    return [rows[index : index + batch_size] for index in range(0, len(rows), batch_size)]


def _images_broken(checks):
    return bool(checks) and all(check.get("status") != 200 for check in checks)


def _shard_shops(shops, shard):
    if not shard:
        return shops
    try:
        index_text, count_text = shard.split("/", 1)
        index, count = int(index_text), int(count_text)
    except (ValueError, AttributeError) as exc:
        raise ValueError("--shard must be K/N") from exc
    if count < 1 or index < 0 or index >= count:
        raise ValueError("--shard must satisfy 0 <= K < N")
    return [shop for position, shop in enumerate(shops) if position % count == index]


def _row_record_id(row):
    return (row.get("record") or {}).get("id")


def _load_jsonl_rows(path):
    rows = []
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _atomic_progress(path, category_set, done):
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(
            {"category_set": category_set, "done": sorted(done)},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _run(shops, args):
    _load_env()
    category_set = getattr(args, "category_set", "apparel")
    shops = _shard_shops(shops, getattr(args, "shard", None))
    resume_dir = getattr(args, "resume", None)
    if resume_dir:
        out_dir = Path(resume_dir)
        if not out_dir.is_dir():
            raise ValueError(f"resume directory does not exist: {out_dir}")
        progress_path = out_dir / "progress.json"
        if not progress_path.is_file():
            raise ValueError("resume directory has no progress.json category_set")
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
        if progress.get("category_set") != category_set:
            raise ValueError(
                f"cannot resume {progress.get('category_set')!r} output as {category_set!r}"
            )
        done = set(progress.get("done") or [])
        shops = [shop for shop in shops if shop["key"] not in done]
        run_id = out_dir.name
    else:
        run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        out_dir = ROOT / "out" / run_id
        out_dir.mkdir(parents=True, exist_ok=True)
        done = set()
        progress_path = out_dir / "progress.json"
        _atomic_progress(progress_path, category_set, done)
    all_path = out_dir / "all.jsonl"
    all_path.touch(exist_ok=True)
    time_budget = getattr(args, "time_budget", None)
    deadline = time.monotonic() + max(0, time_budget) * 60 if time_budget is not None else None
    global_urls, global_ids, all_rows = set(), set(), []
    for path in out_dir.glob("*.jsonl"):
        for row in _load_jsonl_rows(path):
            if row.get("url"):
                global_urls.add(row["url"])
            record_id = _row_record_id(row)
            if record_id:
                global_ids.add(record_id)
    for known_path in getattr(args, "known", None) or []:
        for row in _load_jsonl_rows(known_path):
            if row.get("url"):
                global_urls.add(row["url"])
            record_id = _row_record_id(row)
            if record_id:
                global_ids.add(record_id)
    if all_path.exists():
        all_rows = _load_jsonl_rows(all_path)
    output_lock = threading.Lock()
    reserved_urls, reserved_ids = set(), set()
    summary_rows = []
    connection = None
    client = None
    if args.upload:
        supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
        secret = os.getenv("SUPABASE_SECRET_KEY", "")
        db_url = os.getenv("SUPABASE_DB_URL", "")
        if not (supabase_url and secret and db_url):
            raise RuntimeError(
                "SUPABASE_URL, SUPABASE_SECRET_KEY and SUPABASE_DB_URL are required for --upload"
            )
        import httpx as httpx_module
        import psycopg

        client = httpx_module.Client(timeout=30, follow_redirects=True)
        _ensure_bucket(client, supabase_url, secret)
        connection = psycopg.connect(db_url)

    def run_one(original):
        shop = dict(original)
        shop["_category_set"] = category_set
        max_pages = getattr(args, "max_pages", 0)
        if max_pages:
            for page_key in ("pages", "accessory_pages", "tee_pages"):
                if shop.get(page_key):
                    shop[page_key] = shop[page_key][:max_pages]
            shop["max_pages"] = max_pages
        session = ShopSession(
            shop,
            max_requests=getattr(args, "max_requests", None),
            delay=getattr(args, "delay", None),
            jitter=getattr(args, "jitter", None),
            row_cap=getattr(args, "max_rows_per_shop", 0),
        )
        session.deadline = deadline
        shop_path = out_dir / f"{shop['key']}.jsonl"
        shop_path.touch(exist_ok=True)
        shop_rows = []
        shop_urls = set()

        def save_rows(rows):
            accepted = []
            with output_lock:
                for row in rows:
                    if session.full:
                        break
                    url = row["url"]
                    record_id = _row_record_id(row)
                    if (
                        url in shop_urls
                        or url in global_urls
                        or url in reserved_urls
                        or (record_id and (record_id in global_ids or record_id in reserved_ids))
                    ):
                        continue
                    shop_urls.add(url)
                    if record_id:
                        reserved_ids.add(record_id)
                    reserved_urls.add(url)
                    accepted.append(row)
                    shop_rows.append(row)
                    if session.row_cap and len(shop_rows) >= session.row_cap:
                        session.full = True
            if accepted:
                with shop_path.open("a", encoding="utf-8", newline="\n") as stream:
                    for row in accepted:
                        stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                    stream.flush()

        session.on_emit = save_rows
        status, reason = "ok", ""
        try:
            adapter = REGISTRY[shop["adapter"]]
            adapter(shop, session)
            if shop.get("_status"):
                status, reason = shop["_status"], shop.get("_reason", "")
            elif session.skipped and not shop_rows:
                status, reason = "robots", "All listing URLs disallowed by robots.txt"
            elif any(item.get("status") != 200 for item in session.requests) and not shop_rows:
                status, reason = "error", "One or more listing requests failed"
            elif not shop_rows:
                label = {"accessories": "accessories", "tees": "tees"}.get(category_set, "clothing")
                status, reason = "empty", f"No accepted GBP {label} rows"
        except Blocked as exc:
            status, reason = exc.status, str(exc)
        except TimeoutError as exc:
            status, reason = "error", str(exc)
        except Exception as exc:  # noqa: BLE001
            status, reason = "error", f"{type(exc).__name__}: {exc}"

        checks = []
        check_images = getattr(args, "check_images", 2)
        if check_images:
            for row in shop_rows[:check_images]:
                try:
                    response = httpx.get(
                        row["image_url_original"], timeout=20, follow_redirects=True
                    )
                    checks.append(
                        {"url": row["image_url_original"], "status": response.status_code}
                    )
                except httpx.HTTPError as exc:
                    checks.append({"url": row["image_url_original"], "status": type(exc).__name__})
        images_broken = _images_broken(checks)
        if images_broken:
            status = "images_broken"
            reason = "Every sampled image check failed"
            with output_lock:
                for row in shop_rows:
                    reserved_urls.discard(row["url"])
                    record_id = _row_record_id(row)
                    if record_id:
                        reserved_ids.discard(record_id)
        else:
            with output_lock:
                for row in shop_rows:
                    global_urls.add(row["url"])
                    record_id = _row_record_id(row)
                    if record_id:
                        global_ids.add(record_id)
                        reserved_ids.discard(record_id)
                    reserved_urls.discard(row["url"])
                    all_rows.append(row)
                if shop_rows:
                    with all_path.open("a", encoding="utf-8", newline="\n") as stream:
                        for row in shop_rows:
                            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                        stream.flush()
            if args.upload and shop_rows:
                upload_rows(
                    client,
                    connection,
                    shop_rows,
                    shop,
                    os.environ["SUPABASE_URL"],
                    os.environ["SUPABASE_SECRET_KEY"],
                )
        prices = [float(row["price_gbp"]) for row in shop_rows]
        split = {}
        for row in shop_rows:
            split[row["gender"]] = split.get(row["gender"], 0) + 1
        result = {
            "key": shop["key"],
            "status": status,
            "reason": reason,
            "requests": session.requests,
            "skipped_robots": session.skipped,
            "rows": len(shop_rows),
            "gender_split": split,
            "price_min": min(prices) if prices else None,
            "price_max": max(prices) if prices else None,
            "sample": {
                "title": shop_rows[0]["title"],
                "price_gbp": shop_rows[0]["price_gbp"],
                "url": shop_rows[0]["url"],
                "image": shop_rows[0]["image_url_original"],
            }
            if shop_rows
            else None,
            "image_checks": checks,
            "output": str(shop_path),
        }
        if shop.get("price_country"):
            result["price_country_report"] = shop.get("_price_country_report")
        return result

    try:
        with ThreadPoolExecutor(max_workers=max(1, getattr(args, "workers", 8))) as pool:
            futures = {pool.submit(run_one, shop): shop for shop in shops}
            for future in as_completed(futures):
                result = future.result()
                summary_rows.append(result)
                done.add(result["key"])
                _atomic_progress(progress_path, category_set, done)
    finally:
        if connection:
            connection.close()
        if client:
            client.close()

    summary_rows.sort(key=lambda item: item["key"])
    batch_size = max(1, getattr(args, "batch_size", 1000))
    for index, rows in enumerate(split_batches(all_rows, batch_size), start=1):
        batch = out_dir / f"batch_{index:03}.jsonl"
        with batch.open("w", encoding="utf-8", newline="\n") as stream:
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    summary = {
        "run_id": run_id,
        "total_rows": len(all_rows),
        "shops": summary_rows,
        "all_jsonl": str(all_path),
        "batch_size": batch_size,
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "summary.md").write_text(_markdown(summary), encoding="utf-8")
    print(f"Output: {out_dir}\nRows: {len(all_rows)}")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description="Config-driven UK clothing listing scraper")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--shops", help="comma-separated shop keys")
    parser.add_argument(
        "--category-set",
        choices=("apparel", "accessories", "tees"),
        default="apparel",
    )
    parser.add_argument("--resume", help="reuse an existing output directory")
    parser.add_argument(
        "--known",
        nargs="+",
        metavar="PATH",
        help="JSONL files used to pre-seed URL and record ID deduplication",
    )
    parser.add_argument("--shard", help="only run shard K/N using selected shop order")
    parser.add_argument("--group")
    parser.add_argument("--uk", action="store_true", help="include shops marked UK-only")
    parser.add_argument(
        "--all-enabled",
        action="store_true",
        help="include all enabled shops regardless of location",
    )
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--max-pages", type=int, default=0)
    parser.add_argument("--max-rows-per-shop", type=int, default=0)
    parser.add_argument("--time-budget", type=float, default=12)
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--max-requests", type=int)
    parser.add_argument("--delay", type=float)
    parser.add_argument("--jitter", type=float)
    parser.add_argument("--check-images", type=int, default=2)
    parser.add_argument("--upload", action="store_true")
    args = parser.parse_args(argv)
    shops = load_shops()
    if args.list:
        _list(shops)
        return
    selected = _shop_filter(shops, args, probe=args.probe)
    _prepare_fx_rates(selected)
    if args.probe:
        _probe(selected, args)
    else:
        _run(selected, args)


if __name__ == "__main__":
    main()
