import argparse
import json
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from app import db
from app.mubit import TaskError
from app.tagging import tag_item


def _tag_one(item: dict[str, Any]) -> tuple[str, int | None, str | None]:
    try:
        tags, meta = tag_item(item)
        db.save_tags(
            item["id"], tags.model_dump(), meta.request_id, item.get("image_path")
        )
        return "ok", meta.latency_ms, None
    except TaskError as exc:
        message = f"{exc}; request_ids={','.join(exc.request_ids)}"
        db.save_tag_error(item["id"], message)
        return "failed", None, f"{item['id']}: {message}"
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        db.save_tag_error(item["id"], message)
        return "failed", None, f"{item['id']}: {message}"


def tag_untagged(limit: int, concurrency: int) -> dict[str, Any]:
    if concurrency < 1 or concurrency > 4:
        raise ValueError("concurrency must be between 1 and 4")
    db.init_db()
    items = db.untagged_items(limit)
    ok = failed = 0
    latencies: list[int] = []
    errors: list[str] = []
    fatal_credit_error = False
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {executor.submit(_tag_one, item): item for item in items}
        for future in as_completed(futures):
            status, latency, error = future.result()
            if status == "ok":
                ok += 1
                if latency is not None:
                    latencies.append(latency)
                continue
            failed += 1
            if error:
                errors.append(error)
            item = futures[future]
            error_text = db.get_item(item["id"]).get("tag_error", "")
            if "HTTP 402:" in error_text or "HTTP 403:" in error_text:
                fatal_credit_error = True
                for pending in futures:
                    pending.cancel()
                break
    result = {
        "selected": len(items),
        "ok": ok,
        "failed": failed,
        "p50_latency_ms": int(statistics.median(latencies)) if latencies else None,
        "credit_error": fatal_credit_error,
        "errors": errors[:20],
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Tag untagged catalog items with Mubit")
    parser.add_argument("--limit", type=int, default=100000)
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be positive")
    result = tag_untagged(args.limit, args.concurrency)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["credit_error"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
