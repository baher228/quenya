import base64
import hashlib
import io
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Annotated, Any

import numpy as np
from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from PIL import Image, ImageOps

from app import db, images
from app.accessories import is_accessory_slot
from app.config import CONTEXT_SOURCES, SHARED_CORE_ENABLED, SHOP_TIERS, SPORT_CONTEXTS
from app.core import as_profile, shared_core
from app.embedding_ranker import (
    catalogue_sample,
    choose_bootdiv,
    fallback_arm,
    history_query,
    rank_candidates,
)
from app.llm import TaskError, call_task
from app.outfits import (
    base_layer_subtype,
    build_pool,
    is_outfit_eligible,
    layer_role,
    order_outfit_pieces,
    outfit_errors,
    stylist_user_content,
    validate_outfits,
)
from app.prompts import READER_MODEL, READER_SYSTEM, STYLIST_MODEL, STYLIST_SYSTEM
from app.rank import (
    _is_secondhand,
    add_exploration,
    make_deck,
    rank_items,
    score_item,
    secondhand_share,
    tags_for,
)
from app.routing import route_input
from app.schemas import (
    BoardBriefRequest,
    BoardLabelRequest,
    BoardMergeRequest,
    CheckoutRequest,
    InspirationRead,
    LiveSearchRequest,
    OutfitRequest,
    ProfileCreateRequest,
    RouteCorrectionRequest,
    StylistOutput,
    SwapRequest,
    SwipeRequest,
    UploadAssignRequest,
    VerdictRequest,
)
from app.sources import catalog, retail, sport, vinted
from app.styles import decode_brief, style_notes, style_queries
from app.tagging import tag_item_with_vectors
from app.taste import create_taste, update_taste

router = APIRouter()


def _core() -> dict[str, Any]:
    if SHARED_CORE_ENABLED:
        return shared_core()
    return {"rules": [], "likes": {}, "avoids": {}, "palette": [], "sizes": {}}


def _gender_code(gender: str) -> str:
    return {"menswear": "m", "womenswear": "w", "both": "u"}.get(gender, gender)


def _label_from_brief(text: str) -> str | None:
    delimiters = (":", ",", ";", ".", "—", "–", "-", "\n")
    positions = [text.find(delimiter) for delimiter in delimiters]
    positions = [position for position in positions if position >= 0]
    heading = text[: min(positions)] if positions else text
    label = " ".join(heading.strip().split()[:4]).strip()
    if len(label) < 2:
        return None
    label = (label[0].upper() + label[1:])[:32].strip()
    return label if len(label) >= 2 else None


def _unique_board_label(label: str) -> str:
    existing = {board["label"].casefold() for board in db.profile_boards()}
    if label.casefold() not in existing:
        return label
    suffix = 2
    while f"{label} {suffix}".casefold() in existing:
        suffix += 1
    return f"{label} {suffix}"


def _http_task_error(exc: TaskError) -> HTTPException:
    return HTTPException(
        status_code=502,
        detail={"error": str(exc), "request_ids": exc.request_ids},
    )


def _user_profile() -> dict[str, Any]:
    profile = db.active_profile("user")
    if not profile:
        raise HTTPException(status_code=409, detail="Create a profile first")
    return profile


def _board_summary(profile: dict[str, Any]) -> dict[str, Any]:
    with db.db_connection() as connection:
        counts = {
            "swipes": connection.execute(
                "SELECT COUNT(*) FROM swipes WHERE profile_id=?", (profile["id"],)
            ).fetchone()[0],
            "outfits": connection.execute(
                "SELECT COUNT(*) FROM outfits WHERE profile_id=?", (profile["id"],)
            ).fetchone()[0],
            "uploads": connection.execute(
                "SELECT COUNT(*) FROM uploads WHERE profile_id=?", (profile["id"],)
            ).fetchone()[0],
        }
    return {
        "id": profile["id"],
        "label": profile["label"],
        "context": profile["context"],
        "active": bool(profile["active"]),
        "brief": profile["brief"],
        "version": profile["version"],
        "counts": counts,
    }


def _route_response(
    route_id: str, routed: str, output: Any, request_id: str | None
) -> dict[str, Any]:
    def field(key):
        return output.get(key) if isinstance(output, dict) else getattr(output, key)

    return {
        "id": route_id,
        "routed": routed,
        "context": field("context"),
        "label": field("label"),
        "confidence": field("confidence"),
        "reason": field("reason"),
        "request_id": request_id,
    }


def _start_fetch(
    profile: dict[str, Any], queries: list[str], background_tasks: BackgroundTasks
) -> str:
    queries = list(dict.fromkeys(style_queries(profile.get("brief") or {}) + queries))
    job_id = uuid.uuid4().hex
    job = {
        "id": job_id,
        "started_at": db.now_iso(),
        "status": "running",
        "fetched": 0,
        "tagged": 0,
        "failed": 0,
        "errors": [],
    }
    db.save_fetch_job(job_id, profile["id"], job)
    background_tasks.add_task(
        _fetch_profile_items,
        job_id,
        profile["id"],
        queries,
        profile["brief"]["gender"],
        profile.get("context", "other"),
    )
    return job_id


def _taste(profile_id: str) -> dict[str, Any]:
    current = db.latest_taste(profile_id)
    if not current:
        raise HTTPException(status_code=409, detail="Profile has no taste version")
    return current["profile"]


def _taste_update_result(profile: dict[str, Any]) -> dict[str, Any]:
    try:
        version, updated, meta, _ = update_taste(profile)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except TaskError as exc:
        raise _http_task_error(exc) from exc
    return {
        "version": version,
        "profile": updated,
        "changes": updated["changes"],
        "request_id": meta.request_id,
        "latency_ms": meta.latency_ms,
    }


DECK_CANDIDATE_LIMIT = 5000
DECK_ACCESSORY_LIMIT = 500
DECK_SECONDHAND_LIMIT = 2000


def _deck_candidates(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clothing = [
        item for item in items
        if not is_accessory_slot(tags_for(item).get("slot"))
    ]
    extras = [
        item for item in items
        if is_accessory_slot(tags_for(item).get("slot"))
    ]
    secondhand = []
    new = []
    for item in clothing:
        if _is_secondhand(item):
            secondhand.append(item)
        else:
            new.append(item)
    secondhand_taken = secondhand[:DECK_SECONDHAND_LIMIT]
    new_taken = new[:DECK_CANDIDATE_LIMIT - len(secondhand_taken)]
    return (
        secondhand_taken
        + new_taken
        + extras[:DECK_ACCESSORY_LIMIT]
    )


def _public_item(item: dict[str, Any]) -> dict[str, Any]:
    result = dict(item)
    tags = result.get("tags")
    if not isinstance(tags, dict):
        try:
            tags = json.loads(result.get("tags_json") or "null")
        except (TypeError, json.JSONDecodeError):
            tags = None
    result.pop("tags_json", None)
    result.pop("raw_json", None)
    result["tags"] = tags
    image_url = result.get("image_url")
    local_image = f"/img/{images.image_key(image_url)}.jpg" if image_url else None
    result["image"] = local_image
    result["image_url"] = local_image
    result["origin"] = "live" if str(result.get("query") or "").startswith("live:") else "snapshot"
    result["condition"] = result.get("condition_hint")
    return result


def _public_outfit(outfit: dict[str, Any]) -> dict[str, Any]:
    pieces = order_outfit_pieces(
        [
            item
            for item_id in outfit["piece_ids"]
            if (item := db.get_item_with_tags(item_id))
        ]
    )
    return {
        "id": outfit["id"],
        "title": outfit["title"],
        "mood": outfit["mood"],
        "why": outfit["why"],
        "pieces": [_public_item(item) for item in pieces],
        "total_gbp": outfit["total_gbp"],
        "verdict": outfit.get("verdict"),
        "reason": outfit.get("reason"),
        "request_id": outfit.get("request_id"),
        "taste_version": outfit.get("taste_version"),
    }


def _tag_pending_live(
    profile_id: str,
) -> tuple[int, int, list[str], dict[str, dict[str, int]]]:
    with db.db_connection() as connection:
        pending = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM items WHERE query=? AND tags_json IS NULL AND tag_error IS NULL",
                (f"live:{profile_id}",),
            ).fetchall()
        ]
    tagged = failed = 0
    errors = []
    domain_counts: dict[str, dict[str, int]] = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(tag_item_with_vectors, item): item for item in pending
        }
        for future, item in futures.items():
            domain = item.get("source", "unknown")
            domain_counts.setdefault(
                domain, {"inserted": 0, "tagged": 0, "failed": 0}
            )
            try:
                tags, meta, vectors = future.result()
                db.save_tags(
                    item["id"], tags.model_dump(), meta.request_id,
                    item["image_path"], vectors=vectors,
                )
                tagged += 1
                domain_counts[domain]["tagged"] += 1
            except Exception as exc:
                db.save_tag_error(item["id"], str(exc))
                failed += 1
                domain_counts[domain]["failed"] += 1
                errors.append(f"{item['id']}: {exc}")
    return tagged, failed, errors, domain_counts


def _fetch_profile_items(
    job_id: str, profile_id: str, queries: list[str], gender: str, context: str = "other",
    brief: dict[str, Any] | None = None,
) -> None:
    job = db.get_fetch_job(job_id) or {
        "id": job_id,
        "started_at": db.now_iso(),
        "fetched": 0,
        "tagged": 0,
        "failed": 0,
        "errors": [],
    }
    job["status"] = "running"
    job.setdefault("errors", [])
    db.update_fetch_job(job_id, job)
    try:
        source_domains = CONTEXT_SOURCES.get(
            context, CONTEXT_SOURCES["other"]
        )
        source_domains = list(source_domains)
        profile = db.get_profile(profile_id)
        brief = brief or (profile.get("brief") if profile else {}) or {}
        current_taste = db.latest_taste(profile_id)
        taste_profile = current_taste["profile"] if current_taste else {}
        old_money_style = catalog.is_old_money(brief, taste_profile)
        if old_money_style:
            source_domains.extend(
                shop for shop in catalog.OLD_MONEY_SHOPS if shop not in source_domains
            )
        cached_urls = db.source_urls()
        records: list[dict[str, Any]] = []
        for query in queries[:4]:
            if "vinted" in source_domains:
                try:
                    records.extend(vinted.search(query, _gender_code(gender), per_page=5))
                except Exception as exc:
                    job["errors"].append(f"vinted: {exc}"[:300])
        old_money_queries = []
        if old_money_style and "vinted" in source_domains:
            old_money_queries = catalog.old_money_queries(3)
            for query in old_money_queries:
                try:
                    records.extend(vinted.search(query, _gender_code(gender), per_page=5))
                except Exception as exc:
                    job["errors"].append(f"vinted: {exc}"[:300])
        if context in SPORT_CONTEXTS:
            sport_records, sport_report = sport.search_context(
                queries[:4], context, _gender_code(gender),
                cached_urls=cached_urls,
            )
            records.extend(sport_records)
            job["sport_report"] = sport_report
            if "asos.com" in source_domains:
                for query in queries[:1]:
                    records.extend(
                        retail.search(
                            query, 5, _gender_code(gender),
                            include_domains=["asos.com"],
                            cached_urls=cached_urls,
                        )
                    )
        else:
            for query in queries[:2]:
                records.extend(
                    retail.search(
                        query, 2, _gender_code(gender),
                        include_domains=[
                            domain for domain in source_domains
                            if domain in retail.RETAIL_DOMAINS and domain not in catalog.CATALOG
                        ],
                        cached_urls=cached_urls,
                    )
                )
            per_piece = float(brief.get("budget_gbp") or 0) / (
                1 if brief.get("looking_for") == "pieces" else 4
            )
            pieces = brief.get("pieces") or []
            tavily_left = 2
            catalog_report = []
            for domain in source_domains:
                if domain not in catalog.CATALOG:
                    continue
                tier = SHOP_TIERS.get(domain)
                if tier == "luxury" and per_piece < 150:
                    continue
                if tier == "premium" and per_piece < 40:
                    continue
                found, report = catalog.crawl(
                    domain, _gender_code(gender), 4, cached_urls, tavily_left,
                    pieces=pieces,
                )
                records.extend(found)
                tavily_left = max(0, tavily_left - report.get("tavily_urls_used", 0))
                catalog_report.append(report)
            job["catalog_report"] = catalog_report
        job["old_money_queries"] = old_money_queries
        for record in records:
            record["query"] = f"live:{profile_id}"
        inserted_sources: dict[str, int] = {}
        for source in {
            record["source"]
            for record in records
            if record["source_url"] not in cached_urls
        }:
            inserted_sources[source] = sum(
                1
                for record in records
                if record["source"] == source and record["source_url"] not in cached_urls
            )
        inserted = db.insert_items(records)
        tagged, failed, tag_errors, domain_counts = _tag_pending_live(profile_id)
        for source, amount in inserted_sources.items():
            domain_counts.setdefault(
                source, {"inserted": 0, "tagged": 0, "failed": 0}
            )
            domain_counts[source]["inserted"] += amount
        job.update(
            status="done",
            fetched=inserted,
            tagged=tagged,
            failed=failed,
            errors=[*job["errors"], *tag_errors],
            domain_counts=domain_counts,
        )
        db.update_fetch_job(job_id, job)
    except Exception as exc:
        job.update(status="failed", errors=[*job.get("errors", []), str(exc)])
        db.update_fetch_job(job_id, job)


def _refill_vinted(job_id: str, profile_id: str, page: int) -> None:
    job = db.get_fetch_job(job_id) or {
        "id": job_id,
        "started_at": db.now_iso(),
        "status": "running",
        "fetched": 0,
        "tagged": 0,
        "failed": 0,
        "errors": [],
        "kind": "vinted_refill",
        "page": page,
    }
    job.setdefault("errors", [])
    try:
        profile = db.get_profile(profile_id)
        brief = profile["brief"]
        current_taste = db.latest_taste(profile_id)
        taste_profile = current_taste["profile"] if current_taste else {}
        queries = list(
            dict.fromkeys(
                [*(taste_profile.get("search_queries") or []), brief["text"]]
            )
        )[:3]
        records: list[dict[str, Any]] = []
        for query in queries:
            try:
                records.extend(
                    vinted.search(
                        query,
                        _gender_code(brief["gender"]),
                        per_page=20,
                        page=page,
                    )
                )
            except Exception as exc:
                job["errors"].append(f"vinted: {exc}"[:300])
        for record in records:
            record["query"] = f"live:{profile_id}"
        inserted = db.insert_items(records)
        tagged, failed, tag_errors, domain_counts = _tag_pending_live(profile_id)
        domain_counts.setdefault(
            "vinted", {"inserted": 0, "tagged": 0, "failed": 0}
        )
        domain_counts["vinted"]["inserted"] += inserted
        job.update(
            status="done",
            fetched=inserted,
            tagged=tagged,
            failed=failed,
            errors=[*job["errors"], *tag_errors],
            domain_counts=domain_counts,
        )
    except Exception as exc:
        job.update(status="failed", errors=[*job.get("errors", []), str(exc)])
    db.update_fetch_job(job_id, job)


@router.post("/api/profile")
def create_profile(
    body: ProfileCreateRequest, background_tasks: BackgroundTasks
) -> dict[str, Any]:
    brief = body.brief.model_dump(exclude_none=True)
    brief["style_refs"] = decode_brief(brief)
    try:
        route_output, existing, route_meta, _content = route_input("brief", brief["text"])
    except TaskError as exc:
        raise _http_task_error(exc) from exc
    route_note_output: Any = route_output
    if existing and not body.new_board:
        profile_id = existing["id"]
        profile = db.activate_profile(profile_id)
        db.update_profile_brief(profile_id, brief)
        route_id = db.create_route(
            "user", "brief", brief["text"],
            route_output.model_dump() | {
                "input_brief": brief,
                "_previous_brief": existing["brief"],
                "_routed": "existing",
            },
            route_meta.request_id, profile_id,
        )
        db.add_correction(
            profile_id, "brief", route_id,
            existing["brief"], brief, brief["text"], route_meta.request_id,
        )
        profile = db.get_profile(profile_id)
        current = db.latest_taste(profile_id)
        queries = list(
            dict.fromkeys(
                (current["profile"].get("search_queries") or []) + [brief["text"]]
            )
        )
        job_id = _start_fetch(profile, queries, background_tasks)
        taste = {"version": current["version"], "profile": current["profile"]}
        taste_request_id = route_meta.request_id
        routed = "existing"
    else:
        profile_id = uuid.uuid4().hex
        label = route_output.label
        if body.new_board and existing is not None:
            label = _label_from_brief(brief["text"]) or label
        label = _unique_board_label(label)
        try:
            taste_profile, taste_meta = create_taste(brief, profile_id)
        except TaskError as exc:
            raise _http_task_error(exc) from exc
        db.create_profile(
            profile_id, brief, label=label, context=route_output.context,
            ranker_arm=fallback_arm(profile_id),
        )
        route_output_data = route_output.model_dump() | {
            "input_brief": brief,
            "label": label,
            "_routed": "new",
        }
        route_note_output = route_output_data
        if body.new_board:
            route_output_data["_forced_new"] = True
        route_id = db.create_route(
            "user", "brief", brief["text"],
            route_output_data,
            route_meta.request_id, profile_id,
        )
        profile = db.get_profile(profile_id)
        job_id = _start_fetch(
            profile,
            taste_profile.get("search_queries", []) + [brief["text"]],
            background_tasks,
        )
        taste = {"version": 1, "profile": taste_profile}
        taste_request_id = taste_meta.request_id
        routed = "new"
    board = next(item for item in db.profile_boards() if item["id"] == profile_id)
    return {
        "profile_id": profile_id,
        "brief": brief,
        "taste": taste,
        "version": taste["version"],
        "request_id": taste_request_id,
        "fetch_job_id": job_id,
        "board": _board_summary(board),
        "route": _route_response(
            route_id, routed, route_note_output, route_meta.request_id
        ),
    }


@router.get("/api/boards")
def boards() -> list[dict[str, Any]]:
    return [_board_summary(board) for board in db.profile_boards()]


@router.put("/api/boards/{board_id}/brief")
def update_board_brief(
    board_id: str, body: BoardBriefRequest, background_tasks: BackgroundTasks
) -> dict[str, Any]:
    previous = db.get_profile(board_id, kind="user")
    if not previous:
        raise HTTPException(status_code=404, detail="Board not found")

    brief = body.brief.model_dump(exclude_none=True)
    brief["style_refs"] = decode_brief(brief)
    profile = db.activate_profile(board_id, kind="user")
    if not profile:
        raise HTTPException(status_code=404, detail="Board not found")
    db.update_profile_brief(board_id, brief)
    db.add_correction(
        board_id,
        "brief",
        board_id,
        previous["brief"],
        brief,
        brief["text"],
        None,
    )

    profile = db.get_profile(board_id, kind="user")
    taste_result = _taste_update_result(profile)
    taste = {
        "version": taste_result["version"],
        "profile": taste_result["profile"],
    }
    queries = list(
        dict.fromkeys(
            (taste["profile"].get("search_queries") or []) + [brief["text"]]
        )
    )
    job_id = _start_fetch(profile, queries, background_tasks)
    board = next(item for item in db.profile_boards() if item["id"] == board_id)
    return {
        "profile_id": board_id,
        "brief": brief,
        "taste": taste,
        "version": taste["version"],
        "request_id": taste_result["request_id"],
        "fetch_job_id": job_id,
        "board": _board_summary(board),
        "route": None,
    }


@router.post("/api/boards/{board_id}/activate")
def activate_board(board_id: str) -> dict[str, Any]:
    profile = db.activate_profile(board_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Board not found")
    board = next(item for item in db.profile_boards() if item["id"] == board_id)
    return {"board": _board_summary(board)}


@router.patch("/api/boards/{board_id}")
def rename_board(board_id: str, body: BoardLabelRequest) -> dict[str, Any]:
    profile = db.get_profile(board_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Board not found")
    db.set_profile_label(board_id, body.label)
    board = next(item for item in db.profile_boards() if item["id"] == board_id)
    return {"board": _board_summary(board)}


@router.get("/api/core")
def get_core() -> dict[str, Any]:
    return _core()


def _new_board(brief: dict[str, Any], label: str, context: str) -> dict[str, Any]:
    profile_id = uuid.uuid4().hex
    try:
        create_taste(brief, profile_id)
    except TaskError as exc:
        raise _http_task_error(exc) from exc
    db.create_profile(
        profile_id, brief, label=label, context=context,
        ranker_arm=fallback_arm(profile_id),
    )
    return next(board for board in db.profile_boards() if board["id"] == profile_id)


def _taste_payload(profile_id: str) -> dict[str, Any] | None:
    taste = db.latest_taste(profile_id)
    if not taste:
        return None
    return {"version": taste["version"], "profile": taste["profile"]}


@router.post("/api/route/{route_id}/correct")
def correct_route(route_id: str, body: RouteCorrectionRequest) -> dict[str, Any]:
    route = db.get_route(route_id)
    if not route:
        raise HTTPException(status_code=404, detail="Route not found")
    output = route["output"]
    original_id = route.get("chosen_board_id")
    original = db.get_profile(original_id) if original_id else None
    new_board = None
    if body.board_id:
        target = next(
            (board for board in db.profile_boards() if board["id"] == body.board_id),
            None,
        )
        if not target:
            raise HTTPException(status_code=404, detail="Board not found")
    elif route["input_kind"] == "brief" and output.get("_routed") == "existing":
        brief = output.get("input_brief") or {}
        new_board = _new_board(
            brief,
            body.label or output.get("label", "Your style"),
            output.get("context", "other"),
        )
        target = new_board
        if original:
            previous_brief = output.get("_previous_brief")
            if previous_brief:
                db.update_profile_brief(original_id, previous_brief)
            db.remove_pending_brief_correction(original_id, route_id)
    elif route["input_kind"] == "photo" and body.board_id is None:
        brief = dict(output.get("active_brief") or {})
        brief["text"] = (output.get("read") or {}).get("one_line", route["input_text"])
        new_board = _new_board(
            brief,
            body.label or output.get("label", "Your style"),
            output.get("context", "other"),
        )
        target = new_board
    elif original:
        target = next(
            board for board in db.profile_boards() if board["id"] == original_id
        )
    else:
        raise HTTPException(status_code=400, detail="Correction needs a board")

    if body.label:
        db.set_profile_label(target["id"], body.label)
        target["label"] = body.label

    if original and original_id != target["id"] and output.get("_routed") == "new":
        db.merge_profile_data(original_id, target["id"])

    if route["input_kind"] == "brief" and original_id != target["id"] and not new_board:
        if original_id:
            db.remove_pending_brief_correction(original_id, route_id)
        db.add_correction(
            target["id"], "brief", route_id, {}, {"context": target["context"]},
            route["input_text"], route["request_id"],
        )
    if route["input_kind"] == "photo":
        upload_id = output.get("upload_id")
        if upload_id:
            db.assign_upload(upload_id, target["id"])
            try:
                version, profile, taste_meta, _ = update_taste(db.get_profile(target["id"]))
                taste_result = {
                    "version": version,
                    "profile": profile,
                    "changes": profile["changes"],
                    "request_id": taste_meta.request_id,
                }
            except ValueError:
                taste_result = _taste_payload(target["id"])
            except TaskError as exc:
                raise _http_task_error(exc) from exc
        else:
            taste_result = _taste_payload(target["id"])
    else:
        taste_result = _taste_payload(target["id"])
    db.activate_profile(target["id"])
    correction = {
        "board_id": target["id"],
        "context": target["context"],
        "label": target["label"],
    }
    db.correct_route(route_id, correction)
    target = next(board for board in db.profile_boards() if board["id"] == target["id"])
    return {
        "route": _route_response(route_id, "corrected", route["output"], route["request_id"]),
        "board": _board_summary(target),
        "taste": taste_result,
    }


@router.post("/api/boards/{board_id}/merge")
def merge_board(board_id: str, body: BoardMergeRequest) -> dict[str, Any]:
    if board_id == body.into_id:
        raise HTTPException(status_code=400, detail="Cannot merge a board into itself")
    boards_by_id = {board["id"]: board for board in db.profile_boards()}
    source, target = boards_by_id.get(board_id), boards_by_id.get(body.into_id)
    if not source or not target:
        raise HTTPException(status_code=404, detail="Board not found")
    db.merge_profile_data(board_id, body.into_id)
    db.activate_profile(body.into_id)
    taste_result = _taste_payload(body.into_id)
    if any(db.unconsumed_signals(body.into_id).values()):
        try:
            version, profile, meta, _ = update_taste(db.get_profile(body.into_id))
            taste_result = {
                "version": version,
                "profile": profile,
                "changes": profile["changes"],
                "request_id": meta.request_id,
            }
        except TaskError as exc:
            raise _http_task_error(exc) from exc
    target = next(board for board in db.profile_boards() if board["id"] == body.into_id)
    return {"board": _board_summary(target), "taste": taste_result}


@router.get("/api/fetch/{job_id}")
def fetch_job(job_id: str) -> dict[str, Any]:
    job = db.get_fetch_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Fetch job not found")
    return {
        "status": "done" if job.get("status") == "complete" else job.get("status"),
        "fetched": job.get("fetched", 0),
        "tagged": job.get("tagged", 0),
        "failed": job.get("failed", 0),
        "errors": job.get("errors", []),
        "domain_counts": job.get("domain_counts", {}),
        "sport_report": job.get("sport_report", {}),
        "catalog_report": job.get("catalog_report", []),
        "old_money_queries": job.get("old_money_queries", []),
    }


def _fetch_job_age_seconds(job: dict[str, Any], now: datetime) -> float | None:
    try:
        started_at = datetime.fromisoformat(job["started_at"])
    except (KeyError, TypeError, ValueError):
        return None
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    return (now - started_at.astimezone(UTC)).total_seconds()


def _schedule_vinted_refill(
    active: dict[str, Any],
    share: float,
    live_items: list[dict[str, Any]],
    swiped: set[str],
    background_tasks: BackgroundTasks,
) -> None:
    context = active.get("context", "other")
    if share <= 0 or "vinted" not in CONTEXT_SOURCES.get(
        context, CONTEXT_SOURCES["other"]
    ):
        return
    unseen_vinted = sum(
        item.get("source") == "vinted" and item.get("id") not in swiped
        for item in live_items
    )
    jobs = db.fetch_jobs(active["id"], kind="vinted_refill")
    now = datetime.now(UTC)
    ages = [(job, _fetch_job_age_seconds(job, now)) for job in jobs]
    recent_running = any(
        job.get("status") == "running" and age is not None and 0 <= age <= 300
        for job, age in ages
    )
    if recent_running:
        return
    last_started_age = min(
        (age for _job, age in ages if age is not None), default=None
    )
    if unseen_vinted >= 10 and (
        last_started_age is None or last_started_age <= 600
    ):
        return
    page = 1 + len(jobs) % 5
    job_id = uuid.uuid4().hex
    db.save_fetch_job(
        job_id,
        active["id"],
        {
            "id": job_id,
            "started_at": db.now_iso(),
            "status": "running",
            "fetched": 0,
            "tagged": 0,
            "failed": 0,
            "errors": [],
            "kind": "vinted_refill",
            "page": page,
        },
    )
    background_tasks.add_task(_refill_vinted, job_id, active["id"], page)


def _ensure_live_vinted_items(
    deck: list[dict[str, Any]],
    ranked: list[dict[str, Any]],
    live_items: list[dict[str, Any]],
    swiped: set[str],
) -> list[dict[str, Any]]:
    live_ids = {
        item["id"] for item in live_items if item.get("source") == "vinted"
    }
    if not deck or not live_ids:
        return deck
    deck_ids = {item["id"] for item in deck}

    own_live_count = sum(item["id"] in live_ids for item in deck)
    needed = min(2, len(deck)) - own_live_count
    if needed <= 0:
        return deck
    candidates = []
    candidate_ids = deck_ids | swiped
    for item in ranked:
        if item["id"] in live_ids and item["id"] not in candidate_ids:
            candidates.append(item)
            candidate_ids.add(item["id"])
    for candidate in candidates[:needed]:
        replaceable = [
            index
            for index, item in enumerate(deck)
            if _is_secondhand(item) and item["id"] not in live_ids
        ]
        if not replaceable:
            replaceable = [
                index for index, item in enumerate(deck) if item["id"] not in live_ids
            ]
        if not replaceable:
            break
        index = replaceable[-1]
        deck_ids.remove(deck[index]["id"])
        deck[index] = candidate
        deck_ids.add(candidate["id"])
    return deck


@router.get("/api/deck")
def deck(
    n: int = 10, background_tasks: BackgroundTasks = None
) -> list[dict[str, Any]]:
    if n < 1 or n > 50:
        raise HTTPException(status_code=422, detail="n must be between 1 and 50")
    background_tasks = background_tasks or BackgroundTasks()
    active = _user_profile()
    taste = _taste(active["id"])
    share = secondhand_share(active["brief"], taste)
    with db.db_connection() as connection:
        swipe_rows = connection.execute(
            "SELECT item_id FROM swipes WHERE profile_id=?", (active["id"],)
        ).fetchall()
    swiped = {row[0] for row in swipe_rows}
    swipe_count = len(swipe_rows)
    # Full catalogue makes the deck ~2x slower until ranking is optimised.
    candidates = _deck_candidates(db.live_catalogue())
    profile_live_items = db.profile_live_items(active["id"])
    candidate_by_id = {item["id"]: item for item in candidates}
    candidate_by_id.update({item["id"]: item for item in profile_live_items})
    candidates = list(candidate_by_id.values())
    _schedule_vinted_refill(
        active, share, profile_live_items, swiped, background_tasks
    )
    configured_arm = os.getenv("QUENYA_RANKER", "ab").lower()
    arm = (
        configured_arm
        if configured_arm in {"tags", "embed"}
        else db.get_or_assign_ranker_arm(
            active["id"], fallback_arm(active["id"], "ab")
        )
    )
    request_number = db.next_deck_request(active["id"])
    core = _core()
    tag_ranked = rank_items(
        candidates,
        active["brief"],
        taste,
        exclude_ids=swiped,
        core_profile=as_profile(core) if SHARED_CORE_ENABLED else None,
    )
    swipes = db.profile_swipes(active["id"])
    recent_item_ids = db.recent_deck_item_ids(active["id"]) if arm == "embed" else []
    if arm == "embed":
        vector_ids = [
            *[swipe["item_id"] for swipe in swipes],
            *[item["id"] for item in tag_ranked[:100]],
            *recent_item_ids,
        ]
        embeddings = db.item_mix_vectors(vector_ids)
    else:
        embeddings = {}
    brief_vector = db.profile_embedding(active["id"]) if arm == "embed" else None
    history = history_query(swipes, embeddings) if arm == "embed" else None
    query = history if history is not None else brief_vector
    nearest_rows = (
        db.nearest_item_mix(query, [item["id"] for item in tag_ranked], k=400)
        if arm == "embed" and query is not None
        else []
    )
    for item_id, vector in nearest_rows:
        embeddings.setdefault(item_id, {})["mix"] = vector
    nearest_ids = [item_id for item_id, _vector in nearest_rows]
    sample = None
    scores = {}
    use_embed = arm == "embed" and any(
        (embeddings.get(item["id"]) or {}).get("mix") is not None
        for item in tag_ranked
    )
    if use_embed:
        sample = catalogue_sample()
        ranked, scores, _history = rank_candidates(
            tag_ranked,
            active["id"],
            taste,
            brief_vector,
            swipes,
            embeddings,
            sample,
            nearest_ids=nearest_ids,
        )
        seed_text = f"{active['id']}:{swipe_count}:{request_number}"
        rng = np.random.default_rng(
            int(hashlib.sha1(seed_text.encode()).hexdigest()[:16], 16)
        )
        recent_vectors = [
            (embeddings.get(item_id) or {}).get("mix")
            for item_id in recent_item_ids
        ]

        def selector(pool, selected):
            return choose_bootdiv(
                pool, selected, swipes, embeddings, scores, brief_vector,
                taste, sample, recent_vectors, rng,
            )

    else:
        ranked = tag_ranked
        selector = None
    initial_deck = make_deck(
        ranked,
        n,
        active["brief"],
        include_shoes=active["brief"].get("include_shoes", True),
        secondhand_share=share,
        selector=selector,
    )
    explored_deck = (
        initial_deck
        if use_embed
        else add_exploration(
            initial_deck, ranked, n, taste, swipes=swipe_count,
            seed=f"{active['id']}:{swipe_count}",
            brief=active["brief"],
        )
    )
    if share > 0:
        explored_deck = _ensure_live_vinted_items(
            explored_deck, ranked, profile_live_items, swiped
        )
    now = db.now_iso()
    db.record_deck_events([
        {
            "id": uuid.uuid4().hex,
            "profile_id": active["id"],
            "item_id": item["id"],
            "arm": arm,
            "policy": "clip32_mix_bootdiv" if use_embed else "tags",
            "position": index,
            "score": float(scores.get(item["id"], score_item(item, taste))),
            "created_at": now,
        }
        for index, item in enumerate(explored_deck, start=1)
    ])
    return [
        {**_public_item(item), "explore": bool(item.get("explore", False))}
        for item in explored_deck
    ]


@router.post("/api/swipe")
def swipe(body: SwipeRequest) -> dict[str, Any]:
    active = _user_profile()
    item = db.get_item_with_tags(body.item_id)
    if not item or not item.get("tags"):
        raise HTTPException(status_code=404, detail="Tagged item not found")
    db.insert_swipe(active["id"], body.item_id, body.liked)
    signals = db.unconsumed_signals(active["id"])
    return {
        "swipes_since_update": len(signals["swipes"]),
        "update_due": len(signals["swipes"]) >= 8,
    }


@router.post("/api/taste/update")
def taste_update() -> dict[str, Any]:
    return _taste_update_result(_user_profile())


@router.get("/api/taste")
def taste_history() -> dict[str, Any]:
    active = _user_profile()
    current = db.latest_taste(active["id"])
    if not current:
        raise HTTPException(status_code=404, detail="Taste profile not found")
    counts = db.signal_counts(active["id"])
    signals = db.unconsumed_signals(active["id"])
    counts["unconsumed"] = sum(len(rows) for rows in signals.values())
    return {
        "profile_id": active["id"],
        "current": {"version": current["version"], "profile": current["profile"]},
        "history": db.get_taste_history(active["id"]),
        "counts": counts,
        "core": _core(),
    }


@router.post("/api/outfits")
def outfits(body: OutfitRequest) -> dict[str, Any]:
    active = _user_profile()
    current = db.latest_taste(active["id"])
    profile = current["profile"]
    items = db.live_catalogue()
    core = _core()
    core_profile = as_profile(core) if SHARED_CORE_ENABLED else None
    pool = build_pool(
        items,
        active["brief"],
        profile,
        body.locked_ids,
        body.exclude_ids,
        core_profile=core_profile,
    )
    if not pool:
        raise HTTPException(status_code=409, detail="No eligible tagged items")
    content = stylist_user_content(
        profile,
        body.count,
        body.locked_ids,
        pool,
        core_rules=(
            [item["rule"] for item in core["rules"]]
            if SHARED_CORE_ENABLED
            else None
        ),
        history=db.swipe_history(active["id"]),
        include_shoes=active["brief"].get("include_shoes", True),
        style_notes=style_notes(active["brief"]),
    )
    request_ids: list[str | None] = []
    try:
        result, meta = call_task(
            "stylist", STYLIST_SYSTEM, STYLIST_MODEL, content, StylistOutput
        )
        request_ids.append(meta.request_id)
        validated, errors = validate_outfits(
            [item.model_dump() for item in result.outfits],
            pool,
            body.count,
            body.locked_ids,
            float(profile["price_ceiling_gbp"]),
            include_shoes=active["brief"].get("include_shoes", True),
        )
        if errors:
            repair = [
                {"role": "user", "content": content},
                {"role": "assistant", "content": result.model_dump_json()},
                {
                    "role": "user",
                    "content": (
                        f"These outfits broke rules: {errors}. "
                        f"Return {body.count} corrected outfits."
                    ),
                },
            ]
            result, repair_meta = call_task(
                "stylist", STYLIST_SYSTEM, STYLIST_MODEL, repair, StylistOutput
            )
            request_ids.append(repair_meta.request_id)
            validated, errors = validate_outfits(
                [item.model_dump() for item in result.outfits],
                pool,
                body.count,
                body.locked_ids,
                float(profile["price_ceiling_gbp"]),
                include_shoes=active["brief"].get("include_shoes", True),
            )
        if not validated:
            raise HTTPException(
                status_code=502,
                detail={"error": "No valid outfits after repair", "request_ids": request_ids},
            )
    except TaskError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "error": str(exc),
                "request_ids": [*request_ids, *exc.request_ids],
            },
        ) from exc
    saved = []
    pieces_by_id = {item["id"]: item for item in pool}
    for outfit in validated:
        outfit["piece_ids"] = [
            item["id"]
            for item in order_outfit_pieces(
                [pieces_by_id[item_id] for item_id in outfit["piece_ids"]]
            )
        ]
        outfit_id = db.save_outfit(
            active["id"], current["version"], outfit, outfit["piece_ids"],
            outfit["total_gbp"], request_ids[-1]
        )
        saved.append(
            {
                "id": outfit_id,
                "title": outfit["title"],
                "mood": outfit["mood"],
                "why": outfit["why"],
                "pieces": [
                    _public_item(pieces_by_id[item_id])
                    for item_id in outfit["piece_ids"]
                    if item_id in pieces_by_id
                ],
                "total_gbp": outfit["total_gbp"],
                "verdict": None,
                "reason": None,
                "request_id": request_ids[-1],
                "taste_version": current["version"],
            }
        )
    return {
        "outfits": saved,
        "taste_version": current["version"],
        "dropped": max(
            0, body.count - len(validated), len(result.outfits) - len(validated)
        ),
        "request_ids": request_ids,
    }


def _interleave_base_candidates(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    subtype_order = ("tee", "shirt", "polo", "other")
    groups = {subtype: [] for subtype in subtype_order}
    for item in candidates:
        groups[base_layer_subtype(item)].append(item)
    interleaved = []
    for index in range(max((len(group) for group in groups.values()), default=0)):
        interleaved.extend(
            groups[subtype][index]
            for subtype in subtype_order
            if index < len(groups[subtype])
        )
    return interleaved


@router.post("/api/outfits/{outfit_id}/swap")
def swap(outfit_id: str, body: SwapRequest) -> dict[str, Any]:
    active = _user_profile()
    outfit = db.get_outfit(outfit_id)
    if not outfit or outfit["profile_id"] != active["id"]:
        raise HTTPException(status_code=404, detail="Outfit not found")
    items = db.live_catalogue()
    existing = [db.get_item_with_tags(item_id) for item_id in outfit["piece_ids"]]
    replaced = next((item for item in existing if item and item["id"] == body.item_id), None)
    if not replaced:
        raise HTTPException(status_code=400, detail="Item is not in this outfit")
    slot = tags_for(replaced).get("slot")
    current = _taste(active["id"])
    core = _core()
    excluded_ids = set(body.exclude_ids)
    include_shoes = active["brief"].get("include_shoes", True)
    before = set(
        outfit_errors([piece for piece in existing if piece], include_shoes)
    )
    other_pieces = [
        item for item in existing if item and item["id"] != body.item_id
    ]
    candidates = []
    for item in rank_items(
        items,
        active["brief"],
        current,
        core_profile=as_profile(core) if SHARED_CORE_ENABLED else None,
    ):
        if (
            tags_for(item).get("slot") != slot
            or layer_role(item) != layer_role(replaced)
            or not is_outfit_eligible(item)
            or item["id"] in outfit["piece_ids"]
        ):
            continue
        total = (
            float(outfit["total_gbp"])
            - float(replaced.get("price_gbp") or 0)
            + float(item.get("price_gbp") or 0)
        )
        if total > float(current["price_ceiling_gbp"]):
            continue
        if set(outfit_errors([*other_pieces, item], include_shoes)) - before:
            continue
        candidates.append(item)
    if layer_role(replaced) == "base":
        candidates = _interleave_base_candidates(candidates)
    replacement = next(
        (item for item in candidates if item["id"] not in excluded_ids),
        None,
    )
    if not replacement:
        raise HTTPException(status_code=409, detail="No eligible same-slot replacement")
    ids = [
        replacement["id"] if item_id == body.item_id else item_id
        for item_id in outfit["piece_ids"]
    ]
    total = outfit["total_gbp"] - replaced["price_gbp"] + replacement["price_gbp"]
    pieces_by_id = {
        item["id"]: item for item in [*existing, replacement] if item
    }
    ordered_ids = [
        item["id"]
        for item in order_outfit_pieces(
            [pieces_by_id[item_id] for item_id in ids if item_id in pieces_by_id]
        )
    ]
    ordered_ids.extend(item_id for item_id in ids if item_id not in pieces_by_id)
    db.update_outfit_pieces(outfit_id, ordered_ids, total)
    updated = db.get_outfit(outfit_id)
    return _public_outfit(updated)


@router.post("/api/outfits/{outfit_id}/verdict")
def verdict(outfit_id: str, body: VerdictRequest) -> dict[str, Any]:
    active = _user_profile()
    outfit = db.get_outfit(outfit_id)
    if not outfit or outfit["profile_id"] != active["id"]:
        raise HTTPException(status_code=404, detail="Outfit not found")
    db.update_outfit_verdict(outfit_id, body.verdict, body.reason)
    if body.verdict == "reject" and body.reason:
        db.add_correction(
            active["id"], "outfit", outfit_id, {}, {"verdict": body.verdict},
            body.reason, outfit.get("request_id")
        )
    try:
        version, profile, meta, _ = update_taste(active)
    except TaskError as exc:
        raise _http_task_error(exc) from exc
    return {
        "outfit": _public_outfit(db.get_outfit(outfit_id)),
        "taste": {
            "version": version,
            "profile": profile,
            "changes": profile["changes"],
            "request_id": meta.request_id,
        },
    }


@router.post("/api/checkout")
def checkout(body: CheckoutRequest) -> dict[str, Any]:
    active = _user_profile()
    outfit = db.get_outfit(body.outfit_id)
    if not outfit or outfit["profile_id"] != active["id"]:
        raise HTTPException(status_code=404, detail="Outfit not found")
    return {
        "order_ref": f"DEMO-{uuid.uuid4().hex[:8].upper()}",
        "outfit_id": outfit["id"],
        "total_gbp": outfit["total_gbp"],
        "lines": [
            {
                "title": item["title"],
                "source": item["source"],
                "source_url": item["source_url"],
                "price_gbp": item["price_gbp"],
            }
            for item in (db.get_item(item_id) for item_id in outfit["piece_ids"])
            if item
        ],
        "simulated": True,
    }


@router.get("/api/search")
def keyword_search(q: str, slot: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    active = _user_profile()
    current = _taste(active["id"])
    needle = q.strip().lower()
    matches = [
        item for item in db.live_catalogue()
        if needle in " ".join(
            [item["title"], str(tags_for(item).get("category", ""))]
            + list(tags_for(item).get("aesthetics", []))
        ).lower()
        and (slot is None or tags_for(item).get("slot") == slot)
    ]
    core = _core()
    ranked = rank_items(
        matches,
        active["brief"],
        current,
        core_profile=as_profile(core) if SHARED_CORE_ENABLED else None,
    )
    return [_public_item(item) for item in ranked[:max(1, min(limit, 100))]]


@router.post("/api/uploads")
async def upload_inspiration(file: Annotated[UploadFile, File()]) -> dict[str, Any]:
    active = _user_profile()
    data = await file.read()
    try:
        with Image.open(io.BytesIO(data)) as original:
            image = ImageOps.exif_transpose(original).convert("RGB")
            image.thumbnail((512, 512), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=80)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid image") from exc
    image_bytes = buffer.getvalue()
    path = images.save_upload(uuid.uuid4().hex, image_bytes)
    image_url = (
        "data:image/jpeg;base64,"
        + base64.b64encode(image_bytes).decode("ascii")
    )
    request_ids = []
    try:
        read, meta = call_task(
            "reader", READER_SYSTEM, READER_MODEL,
            [{"type": "text", "text": "Read this fashion inspiration image."},
             {"type": "image_url", "image_url": {"url": image_url}}],
            InspirationRead,
        )
        request_ids.append(meta.request_id)
        read_data = read.model_dump()
        aesthetics = ", ".join(read_data.get("aesthetics") or [])
        garments = ", ".join(
            f"{item.get('slot', '')} {item.get('category', '')}".strip()
            for item in read_data.get("garments") or []
        )
        input_text = (
            f"\"{read_data['one_line']}\" | aesthetics: {aesthetics} | "
            f"garments: {garments}"
        )
        route_output, selected, route_meta, _content = route_input("photo", input_text)
        request_ids.append(route_meta.request_id)
    except TaskError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "error": str(exc),
                "request_ids": [*request_ids, *exc.request_ids],
            },
        ) from exc
    destination = active["id"] if (
        (selected and selected["id"] == active["id"])
        or (not selected and route_output.confidence < 0.6)
    ) else None
    upload_id = db.add_upload(
        destination, path, read_data, meta.request_id
    )
    route_id = db.create_route(
        "user",
        "photo",
        input_text,
        route_output.model_dump() | {
            "read": read_data,
            "active_brief": active["brief"],
            "upload_id": upload_id,
            "reader_request_id": meta.request_id,
            "_routed": "existing" if destination else "new",
        },
        route_meta.request_id,
        selected["id"] if selected else None,
    )
    taste_result = None
    if destination:
        try:
            version, profile, taste_meta, _ = update_taste(db.get_profile(destination))
            request_ids.append(taste_meta.request_id)
            taste_result = {
                "version": version,
                "profile": profile,
                "changes": profile["changes"],
                "request_id": taste_meta.request_id,
            }
        except TaskError as exc:
            raise HTTPException(
                status_code=502,
                detail={
                    "error": str(exc),
                    "request_ids": [*request_ids, *exc.request_ids],
                },
            ) from exc
    return {
        "upload_id": upload_id,
        "read": read_data,
        "pending": destination is None,
        "route": _route_response(
            route_id, "existing" if destination else "pending",
            route_output, route_meta.request_id,
        ),
        "taste": taste_result,
        "request_ids": request_ids,
    }


@router.post("/api/uploads/{upload_id}/assign")
def assign_upload(upload_id: str, body: UploadAssignRequest) -> dict[str, Any]:
    upload = db.get_upload(upload_id)
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")
    route = db.route_for_upload(upload_id)
    active = _user_profile()
    if body.board_id:
        target = next(
            (board for board in db.profile_boards() if board["id"] == body.board_id),
            None,
        )
        if not target:
            raise HTTPException(status_code=404, detail="Board not found")
    else:
        brief = dict(active["brief"])
        brief["text"] = upload["read"].get("one_line", brief["text"])
        target = _new_board(
            brief,
            body.label or (route["output"].get("label") if route else "Your style"),
            route["output"].get("context", "other") if route else "other",
        )
    db.assign_upload(upload_id, target["id"])
    db.activate_profile(target["id"])
    try:
        version, profile, meta, _ = update_taste(db.get_profile(target["id"]))
    except TaskError as exc:
        raise _http_task_error(exc) from exc
    router_board_id = (
        route.get("chosen_board_id") or route["output"].get("board_id")
        if route else None
    )
    if route and (
        router_board_id != target["id"]
        and not (router_board_id is None and body.board_id is None)
    ):
        db.correct_route(
            route["id"],
            {
                "board_id": target["id"],
                "context": route["output"].get("context", target["context"]),
                "label": body.label or target["label"],
            },
        )
    board = next(item for item in db.profile_boards() if item["id"] == target["id"])
    return {
        "upload_id": upload_id,
        "pending": False,
        "board": _board_summary(board),
        "taste": {
            "version": version,
            "profile": profile,
            "changes": profile["changes"],
            "request_id": meta.request_id,
        },
    }


@router.post("/api/search/live")
def live_search(body: LiveSearchRequest) -> dict[str, Any]:
    active = _user_profile()
    gender = _gender_code(active["brief"]["gender"])
    try:
        route_output, selected, route_meta, _content = route_input("search", body.q)
    except TaskError as exc:
        raise _http_task_error(exc) from exc
    context = selected["context"] if selected else route_output.context
    route_id = db.create_route(
        "user",
        "search",
        body.q,
        route_output.model_dump() | {"_routed": "existing" if selected else "new"},
        route_meta.request_id,
        selected["id"] if selected else None,
    )
    domains = CONTEXT_SOURCES.get(context, CONTEXT_SOURCES["other"])
    cached_urls = db.source_urls()
    items: list[dict[str, Any]] = []
    if "vinted" in domains:
        items.extend(vinted.search(body.q, gender, per_page=10))
    if context in SPORT_CONTEXTS:
        sport_items, sport_report = sport.search_context(
            [body.q], context, gender, cached_urls=cached_urls
        )
        items.extend(sport_items)
        retail_items = (
            retail.search(
                body.q, 10, gender, include_domains=["asos.com"],
                cached_urls=cached_urls,
            )
            if "asos.com" in domains else []
        )
    else:
        sport_report = {}
        retail_items = retail.search(
            body.q,
            2,
            gender,
            include_domains=[
                domain for domain in domains
                if domain in retail.RETAIL_DOMAINS and domain not in catalog.CATALOG
            ],
            cached_urls=cached_urls,
        )
        per_piece = float(active["brief"].get("budget_gbp") or 0) / (
            1 if active["brief"].get("looking_for") == "pieces" else 4
        )
        catalog_report = []
        tavily_left = 4
        for domain in domains:
            if domain not in catalog.CATALOG:
                continue
            tier = SHOP_TIERS.get(domain)
            if tier == "luxury" and per_piece < 150:
                continue
            if tier == "premium" and per_piece < 40:
                continue
            catalog_items, report = catalog.crawl(
                domain, gender, 4, cached_urls, tavily_left,
                pieces=active["brief"].get("pieces") or [],
            )
            retail_items.extend(catalog_items)
            tavily_left = max(0, tavily_left - report.get("tavily_urls_used", 0))
            catalog_report.append(report)
    items.extend(retail_items)
    for item in items:
        item["query"] = f"live:search:{active['id']}"
    db.insert_items(items)
    urls = list(dict.fromkeys(item["source_url"] for item in items))
    if urls:
        placeholders = ",".join("?" for _ in urls)
        with db.db_connection() as connection:
            pending = [
                dict(row)
                for row in connection.execute(
                    f"SELECT * FROM items WHERE source_url IN ({placeholders}) "
                    "AND tags_json IS NULL AND tag_error IS NULL",
                    urls,
                ).fetchall()
            ]
        failures: list[TaskError] = []
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                executor.submit(tag_item_with_vectors, item): item for item in pending
            }
            for future, item in futures.items():
                try:
                    tags, meta, vectors = future.result()
                    db.save_tags(
                        item["id"], tags.model_dump(), meta.request_id,
                        item["image_path"], vectors=vectors,
                    )
                except TaskError as exc:
                    failures.append(exc)
                    db.save_tag_error(item["id"], str(exc))
                except Exception as exc:
                    db.save_tag_error(item["id"], str(exc))
        if failures:
            request_ids = [request_id for failure in failures for request_id in failure.request_ids]
            raise HTTPException(
                status_code=502,
                detail={"error": "Live item tagging failed", "request_ids": request_ids},
            )
    results = [db.get_item_with_tags(item_id) for item in items for item_id in [item["id"]]]
    return {
        "items": [_public_item(item) for item in results if item and item.get("tags")],
        "retail": "fetched" if retail_items else "skipped",
        "route": _route_response(
            route_id,
            "selected" if selected else "new",
            route_output,
            route_meta.request_id,
        ),
        "sport_report": sport_report,
        "catalog_report": catalog_report if context not in SPORT_CONTEXTS else [],
    }


@router.post("/api/reset")
def reset() -> dict[str, Any]:
    with db.db_connection() as connection:
        connection.execute("DELETE FROM fetch_jobs")
        connection.execute("DELETE FROM routes WHERE profile_kind='user'")
        connection.execute(
            "DELETE FROM uploads WHERE profile_id IS NULL OR profile_id IN "
            "(SELECT id FROM profiles WHERE kind='user')"
        )
        connection.execute(
            "DELETE FROM swipes WHERE profile_id IN (SELECT id FROM profiles WHERE kind='user')"
        )
        for table in ("taste_versions", "outfits", "corrections", "uploads"):
            connection.execute(
                f"DELETE FROM {table} "
                "WHERE profile_id IN (SELECT id FROM profiles WHERE kind='user')"
            )
        connection.execute("DELETE FROM profiles WHERE kind='user'")
    return {"ok": True}
