import argparse
import json
from pathlib import Path
from typing import Any

from app import db
from app.config import BACKEND_ROOT
from app.sources import retail, vinted

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


def ingest(source: str, per_query: int) -> dict[str, Any]:
    db.init_db()
    all_records = []
    query_errors = 0
    seeds = read_seeds()
    if source == "retail":
        retail.reset_domain_report()
    for gender, query in seeds:
        try:
            if source == "vinted":
                records = vinted.search(query, gender, per_query)
            else:
                records = retail.search(query, per_query, gender)
            all_records.extend(records)
        except Exception as exc:
            query_errors += 1
            print(f"Query failed ({source}, {gender}|{query}): {type(exc).__name__}: {exc}")
    inserted = db.insert_items(all_records)
    summary: dict[str, Any] = {
        "source": source,
        "queries": len(seeds),
        "records": len(all_records),
        "inserted": inserted,
        "query_errors": query_errors,
    }
    if source == "retail":
        summary["retail_domains"] = retail.get_domain_report()
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest listing records into the catalog")
    parser.add_argument("--source", choices=("vinted", "retail"), required=True)
    parser.add_argument("--per-query", type=int, default=10)
    args = parser.parse_args()
    if args.per_query < 1:
        parser.error("--per-query must be positive")
    print(json.dumps(ingest(args.source, args.per_query), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
