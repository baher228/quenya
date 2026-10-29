import argparse
import contextlib
import io
import json
import random
import sys
from pathlib import Path
from typing import Any

from app import db, embed, tag
from app.config import BACKEND_ROOT
from app.sources import lv, retail, vinted

SEED_PATH = BACKEND_ROOT / "seeds" / "seed_queries.txt"


def read_seeds(path: Path = SEED_PATH) -> list[tuple[str, str]]:
    seeds = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        gender, query = line.split("|", 1)
        seeds.append((gender.strip(), query.strip()))
    return seeds


def select_seeds(
    seeds: list[tuple[str, str]], sample: int | None = None
) -> list[tuple[str, str]]:
    if sample is None or sample >= len(seeds):
        return seeds
    return random.sample(seeds, sample)


def _run_with_logs_on_stderr(func, *args, **kwargs):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        result = func(*args, **kwargs)
    if output.getvalue():
        print(output.getvalue(), file=sys.stderr, end="")
    return result


def ingest(
    source: str,
    per_query: int,
    sample: int | None = None,
    *,
    limit: int = 25,
    run_tag: bool = False,
    run_embed: bool = False,
    tag_concurrency: int = 2,
    embed_batch_size: int = 32,
    embed_limit: int = 2000,
) -> dict[str, Any]:
    db.init_db()
    all_records = []
    query_errors = 0
    lv_report = None
    existing_urls = None
    if source == "lv":
        seeds = []
        existing_urls = db.source_urls()
        all_records, lv_report = _run_with_logs_on_stderr(
            lv.trickle, limit, existing_urls=existing_urls
        )
    else:
        seeds = select_seeds(read_seeds(), sample)
    if source == "retail":
        retail.reset_domain_report()
    if source != "lv":
        for gender, query in seeds:
            try:
                if source == "vinted":
                    records = vinted.search(query, gender, per_query)
                else:
                    records = retail.search(query, per_query, gender)
                all_records.extend(records)
            except Exception as exc:
                query_errors += 1
                print(
                    f"Query failed ({source}, {gender}|{query}): "
                    f"{type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )
        existing_urls = db.source_urls()
    inserted = db.insert_items(all_records)
    summary: dict[str, Any] = {
        "source": source,
        "queries": len(seeds),
        "records": len(all_records),
        "inserted": inserted,
        "query_errors": query_errors,
    }
    if lv_report is not None:
        summary["lv"] = lv_report
    new_item_ids = list(dict.fromkeys(
        item["id"]
        for item in all_records
        if item.get("source_url") not in existing_urls
    ))
    if run_tag:
        summary["tag"] = _run_with_logs_on_stderr(
            tag.tag_untagged,
            len(new_item_ids),
            tag_concurrency,
            source=source,
            item_ids=new_item_ids,
        ) if new_item_ids else {
            "selected": 0,
            "ok": 0,
            "failed": 0,
            "p50_latency_ms": None,
            "credit_error": False,
            "errors": [],
        }
    if run_embed:
        summary["embed"] = _run_with_logs_on_stderr(
            embed.backfill, embed_batch_size, limit=embed_limit
        )
    if source == "retail":
        summary["retail_domains"] = retail.get_domain_report()
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest listing records into the catalog")
    parser.add_argument("--source", choices=("vinted", "retail", "lv"), required=True)
    parser.add_argument("--per-query", type=int, default=10)
    parser.add_argument("--sample", type=int)
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--tag", action="store_true")
    parser.add_argument("--embed", action="store_true")
    parser.add_argument("--tag-concurrency", type=int, default=2)
    parser.add_argument("--embed-batch-size", type=int, default=32)
    parser.add_argument("--embed-limit", type=int, default=2000)
    args = parser.parse_args()
    if args.per_query < 1:
        parser.error("--per-query must be positive")
    if args.sample is not None and args.sample < 1:
        parser.error("--sample must be positive")
    if args.source == "lv" and args.limit < 1:
        parser.error("--limit must be positive")
    if args.tag_concurrency < 1 or args.tag_concurrency > 4:
        parser.error("--tag-concurrency must be between 1 and 4")
    if args.embed_batch_size < 1:
        parser.error("--embed-batch-size must be positive")
    if args.embed_limit < 1:
        parser.error("--embed-limit must be positive")
    print(json.dumps(
        ingest(
            args.source,
            args.per_query,
            args.sample,
            limit=args.limit,
            run_tag=args.tag,
            run_embed=args.embed,
            tag_concurrency=args.tag_concurrency,
            embed_batch_size=args.embed_batch_size,
            embed_limit=args.embed_limit,
        ),
        ensure_ascii=False,
        separators=(",", ":"),
    ))


if __name__ == "__main__":
    main()
