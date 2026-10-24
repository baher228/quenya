import base64
import io
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from PIL import Image, ImageOps

from app import db
from app.config import BACKEND_ROOT, CONTEXT_SOURCES, SPORT_CONTEXTS
from app.core import as_profile, shared_core
from app.images import image_key
from app.mubit import TaskError, call_task
from app.outfits import build_pool, stylist_user_content, validate_outfits
from app.prompts import READER_MODEL, READER_SYSTEM, STYLIST_MODEL, STYLIST_SYSTEM
from app.rank import make_deck, rank_items, tags_for
from app.routing import route_input
from app.schemas import (
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
from app.sources import retail, sport, vinted
from app.tagging import tag_item
from app.taste import create_taste, update_taste

router = APIRouter()
FETCH_JOBS: dict[str, dict[str, Any]] = {}


def _gender_code(gender: str) -> str:
    return {"menswear": "m", "womenswear": "w", "both": "u"}.get(gender, gender)


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
    job_id = uuid.uuid4().hex
    FETCH_JOBS[job_id] = {
        "id": job_id,
        "status": "running",
        "fetched": 0,
        "tagged": 0,
        "failed": 0,
        "errors": [],
    }
    FETCH_JOBS[profile["id"]] = FETCH_JOBS[job_id]
    background_tasks.add_task(
        _fetch_profile_items,
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


def _public_item(item: dict[str, Any]) -> dict[str, Any]:
    result = dict(item)
    tags = result.get("tags")
    if not isinstance(tags, dict):
        try:
            tags = json.loads(result.get("tags_json") or "null")
        except (TypeError, json.JSONDecodeError):
            tags = None
    result.pop("tags_json", None)
    result["tags"] = tags
    image_url = result.get("image_url")
    local_image = f"/img/{image_key(image_url)}.jpg" if image_url else None
    result["image"] = local_image
    result["image_url"] = local_image
    result["origin"] = "live" if str(result.get("query") or "").startswith("live:") else "snapshot"
    result["condition"] = result.get("condition_hint")
    return result


def _public_outfit(outfit: dict[str, Any]) -> dict[str, Any]:
    pieces = [
        item
        for item_id in outfit["piece_ids"]
        if (item := db.get_item_with_tags(item_id))
    ]
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


def _fetch_profile_items(
    profile_id: str, queries: list[str], gender: str, context: str = "other"
) -> None:
    job = FETCH_JOBS[profile_id]
    job["status"] = "running"
    try:
        source_domains = CONTEXT_SOURCES.get(
            context, CONTEXT_SOURCES["other"]
        )
        cached_urls = db.source_urls()
        records: list[dict[str, Any]] = []
        for query in queries[:4]:
            if "vinted" in source_domains:
                records.extend(vinted.search(query, _gender_code(gender), per_page=5))
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
                        query, 5, _gender_code(gender),
                        include_domains=[
                            domain for domain in source_domains
                            if domain in retail.RETAIL_DOMAINS
                        ],
                        cached_urls=cached_urls,
                    )
                )
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
        with db.db_connection() as connection:
            pending = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM items WHERE query=? AND tags_json IS NULL AND tag_error IS NULL",
                    (f"live:{profile_id}",),
                ).fetchall()
            ]
        tagged = failed = 0
        domain_counts: dict[str, dict[str, int]] = {
            source: {"inserted": amount, "tagged": 0, "failed": 0}
            for source, amount in inserted_sources.items()
        }
        errors = []
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(tag_item, item): item for item in pending}
            for future, item in futures.items():
                domain = item.get("source", "unknown")
                domain_counts.setdefault(domain, {"inserted": 0, "tagged": 0, "failed": 0})
                try:
                    tags, meta = future.result()
                    db.save_tags(item["id"], tags.model_dump(), meta.request_id, item["image_path"])
                    tagged += 1
                    domain_counts[domain]["tagged"] += 1
                except Exception as exc:
                    db.save_tag_error(item["id"], str(exc))
                    failed += 1
                    domain_counts[domain]["failed"] += 1
                    errors.append(f"{item['id']}: {exc}")
        job.update(
            status="done",
            fetched=inserted,
            tagged=tagged,
            failed=failed,
            errors=errors,
            domain_counts=domain_counts,
        )
    except Exception as exc:
        job.update(status="failed", errors=[str(exc)])


@router.post("/api/profile")
def create_profile(
    body: ProfileCreateRequest, background_tasks: BackgroundTasks
) -> dict[str, Any]:
    brief = body.brief.model_dump(exclude_none=True)
    try:
        route_output, existing, route_meta, _content = route_input("brief", brief["text"])
    except TaskError as exc:
        raise _http_task_error(exc) from exc
    if existing:
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
        try:
            taste_profile, taste_meta = create_taste(brief, profile_id)
        except TaskError as exc:
            raise _http_task_error(exc) from exc
        db.create_profile(
            profile_id, brief, label=route_output.label, context=route_output.context
        )
        route_id = db.create_route(
            "user", "brief", brief["text"],
            route_output.model_dump() | {"input_brief": brief, "_routed": "new"},
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
        "route": _route_response(route_id, routed, route_output, route_meta.request_id),
    }


@router.get("/api/boards")
def boards() -> list[dict[str, Any]]:
    return [_board_summary(board) for board in db.profile_boards()]


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
    return shared_core()


def _new_board(brief: dict[str, Any], label: str, context: str) -> dict[str, Any]:
    profile_id = uuid.uuid4().hex
    try:
        create_taste(brief, profile_id)
    except TaskError as exc:
        raise _http_task_error(exc) from exc
    db.create_profile(profile_id, brief, label=label, context=context)
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
    job = FETCH_JOBS.get(job_id)
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
    }


@router.get("/api/deck")
def deck(n: int = 10) -> list[dict[str, Any]]:
    if n < 1 or n > 50:
        raise HTTPException(status_code=422, detail="n must be between 1 and 50")
    active = _user_profile()
    taste = _taste(active["id"])
    with db.db_connection() as connection:
        swiped = {
            row[0]
            for row in connection.execute(
                "SELECT item_id FROM swipes WHERE profile_id=?", (active["id"],)
            ).fetchall()
        }
    candidates = db.list_items(limit=5000)
    core = shared_core()
    ranked = rank_items(
        candidates,
        active["brief"],
        taste,
        exclude_ids=swiped,
        core_profile=as_profile(core),
    )
    return [
        _public_item(item)
        for item in make_deck(
            ranked,
            n,
            active["brief"],
            include_shoes=active["brief"].get("include_shoes", True),
        )
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
    active = _user_profile()
    try:
        version, profile, meta, _ = update_taste(active)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except TaskError as exc:
        raise _http_task_error(exc) from exc
    return {
        "version": version,
        "profile": profile,
        "changes": profile["changes"],
        "request_id": meta.request_id,
        "latency_ms": meta.latency_ms,
    }


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
        "core": shared_core(),
    }


@router.post("/api/outfits")
def outfits(body: OutfitRequest) -> dict[str, Any]:
    active = _user_profile()
    current = db.latest_taste(active["id"])
    profile = current["profile"]
    items = db.list_items(limit=5000)
    core = shared_core()
    core_profile = as_profile(core)
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
        core_rules=[item["rule"] for item in core["rules"]],
        include_shoes=active["brief"].get("include_shoes", True),
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
    for outfit in validated:
        outfit_id = db.save_outfit(
            active["id"], current["version"], outfit, outfit["piece_ids"],
            outfit["total_gbp"], request_ids[-1]
        )
        pieces_by_id = {item["id"]: item for item in pool}
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


@router.post("/api/outfits/{outfit_id}/swap")
def swap(outfit_id: str, body: SwapRequest) -> dict[str, Any]:
    active = _user_profile()
    outfit = db.get_outfit(outfit_id)
    if not outfit or outfit["profile_id"] != active["id"]:
        raise HTTPException(status_code=404, detail="Outfit not found")
    items = db.list_items(limit=5000)
    existing = [db.get_item_with_tags(item_id) for item_id in outfit["piece_ids"]]
    replaced = next((item for item in existing if item and item["id"] == body.item_id), None)
    if not replaced:
        raise HTTPException(status_code=400, detail="Item is not in this outfit")
    slot = tags_for(replaced).get("slot")
    current = _taste(active["id"])
    candidates = [
        item for item in rank_items(
            items, active["brief"], current, core_profile=as_profile(shared_core())
        )
        if tags_for(item).get("slot") == slot and item["id"] not in outfit["piece_ids"]
    ]
    replacement = next(
        (
            item for item in candidates
            if outfit["total_gbp"] - replaced["price_gbp"] + item["price_gbp"]
            <= current["price_ceiling_gbp"]
        ),
        None,
    )
    if not replacement:
        raise HTTPException(status_code=409, detail="No eligible same-slot replacement")
    ids = [
        replacement["id"] if item_id == body.item_id else item_id
        for item_id in outfit["piece_ids"]
    ]
    total = outfit["total_gbp"] - replaced["price_gbp"] + replacement["price_gbp"]
    db.update_outfit_pieces(outfit_id, ids, total)
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
        item for item in db.list_items(limit=5000)
        if needle in " ".join(
            [item["title"], str(tags_for(item).get("category", ""))]
            + list(tags_for(item).get("aesthetics", []))
        ).lower()
        and (slot is None or tags_for(item).get("slot") == slot)
    ]
    ranked = rank_items(
        matches, active["brief"], current, core_profile=as_profile(shared_core())
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
            path = BACKEND_ROOT / "data" / "uploads" / f"{uuid.uuid4().hex}.jpg"
            path.parent.mkdir(parents=True, exist_ok=True)
            image.save(path, format="JPEG", quality=80)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid image") from exc
    image_url = "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode("ascii")
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
        destination, str(path), read_data, meta.request_id
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
            10,
            gender,
            include_domains=[
                domain for domain in domains if domain in retail.RETAIL_DOMAINS
            ],
            cached_urls=cached_urls,
        )
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
            futures = {executor.submit(tag_item, item): item for item in pending}
            for future, item in futures.items():
                try:
                    tags, meta = future.result()
                    db.save_tags(item["id"], tags.model_dump(), meta.request_id, item["image_path"])
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
    }


@router.post("/api/reset")
def reset() -> dict[str, Any]:
    with db.db_connection() as connection:
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
    FETCH_JOBS.clear()
    return {"ok": True}
