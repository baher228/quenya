import argparse
import json
from concurrent.futures import ThreadPoolExecutor

from app import db
from app.sources import catalog, vinted
from app.tagging import tag_item


def seed(per_category: int, tavily_budget: int, old_money_queries: int) -> dict:
    cached_urls = db.source_urls()
    with db.db_connection() as connection:
        existing_ids = {
            row["id"] for row in connection.execute("SELECT id FROM items").fetchall()
        }
        llm_before = connection.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]
    fetched: dict[str, int] = {}
    records_by_url = {}
    tavily_left = max(0, tavily_budget)
    crawl_reports = []
    for domain in catalog.CATALOG:
        for gender in ("m", "w"):
            records, report = catalog.crawl(
                domain, gender, per_category, cached_urls, tavily_left
            )
            crawl_reports.append(report)
            tavily_left = max(0, tavily_left - report.get("tavily_urls_used", 0))
            for record in records:
                records_by_url.setdefault(record["source_url"], record)
    for gender in ("m", "w"):
        for query in catalog.old_money_queries(old_money_queries, gender):
            for record in vinted.search(query, gender, per_page=5):
                records_by_url.setdefault(record["source_url"], record)
    records = list(records_by_url.values())
    for record in records:
        source = record["source"]
        fetched[source] = fetched.get(source, 0) + 1
    db.insert_items(records)
    inserted_ids = {
        record["id"] for record in records if record["id"] not in existing_ids
    }
    report = {
        source: {
            "fetched": amount,
            "inserted": 0,
            "tagged": 0,
            "tag_failed": 0,
        }
        for source, amount in fetched.items()
    }
    items = []
    if inserted_ids:
        placeholders = ",".join("?" for _ in inserted_ids)
        with db.db_connection() as connection:
            items = [
                dict(row) for row in connection.execute(
                    f"SELECT * FROM items WHERE id IN ({placeholders}) "
                    "AND tags_json IS NULL AND tag_error IS NULL",
                    tuple(inserted_ids),
                ).fetchall()
            ]
    inserted_by_id = {
        record["id"]: record for record in records if record["id"] in inserted_ids
    }
    for record in inserted_by_id.values():
        report.setdefault(record["source"], {
            "fetched": 0, "inserted": 0, "tagged": 0, "tag_failed": 0,
        })["inserted"] += 1
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(tag_item, item): item for item in items}
        for future, item in futures.items():
            source_report = report.setdefault(item["source"], {
                "fetched": 0, "inserted": 0, "tagged": 0, "tag_failed": 0,
            })
            try:
                tags, meta = future.result()
                db.save_tags(
                    item["id"], tags.model_dump(), meta.request_id, item["image_path"],
                    image_preview=item.get("image_preview"),
                )
                source_report["tagged"] += 1
            except Exception as exc:
                db.save_tag_error(item["id"], str(exc))
                source_report["tag_failed"] += 1
    with db.db_connection() as connection:
        llm_after = connection.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]
    return {
        "sources": report,
        "total_fetched": len(records),
        "total_inserted": len(inserted_ids),
        "total_tagged": sum(entry["tagged"] for entry in report.values()),
        "total_tag_failed": sum(entry["tag_failed"] for entry in report.values()),
        "tavily_urls_used": tavily_budget - tavily_left,
        "tavily_disabled": any(item.get("tavily_disabled") for item in crawl_reports),
        "llm_calls": llm_after - llm_before,
        "crawl_reports": crawl_reports,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-category", type=int, default=15)
    parser.add_argument("--tavily-budget", type=int, default=120)
    parser.add_argument("--old-money-queries", type=int, default=8)
    args = parser.parse_args()
    print(json.dumps(seed(args.per_category, args.tavily_budget, args.old_money_queries), indent=2))


if __name__ == "__main__":
    main()
