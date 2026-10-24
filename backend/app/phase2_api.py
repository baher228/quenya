import base64
import io
import uuid
from concurrent.futures import ThreadPoolExecutor
from time import monotonic
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from PIL import Image, ImageOps

from app import db
from app.config import BACKEND_ROOT
from app.images import image_key
from app.mubit import TaskError, call_task
from app.outfits import build_pool, stylist_user_content, validate_outfits
from app.prompts import READER_MODEL, READER_SYSTEM, STYLIST_MODEL, STYLIST_SYSTEM
from app.rank import make_deck, rank_items, tags_for
from app.schemas import (
    CheckoutRequest,
    InspirationRead,
    LiveSearchRequest,
    OutfitRequest,
    ProfileBrief,
    StylistOutput,
    SwapRequest,
    SwipeRequest,
    VerdictRequest,
)
from app.sources import retail, vinted
from app.tagging import tag_item
from app.taste import create_taste, update_taste

router = APIRouter()
FETCH_JOBS: dict[str, dict[str, Any]] = {}
LIVE_RETAIL_EXECUTOR = ThreadPoolExecutor(max_workers=1)


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


def _taste(profile_id: str) -> dict[str, Any]:
    current = db.latest_taste(profile_id)
    if not current:
        raise HTTPException(status_code=409, detail="Profile has no taste version")
    return current["profile"]


def _public_item(item: dict[str, Any]) -> dict[str, Any]:
    result = dict(item)
    result["image_url"] = f"/img/{image_key(item['image_url'])}.jpg"
    return result


def _fetch_profile_items(profile_id: str, queries: list[str], gender: str) -> None:
    job = FETCH_JOBS[profile_id]
    job["status"] = "running"
    try:
        records = []
        for query in queries:
            records.extend(vinted.search(query, _gender_code(gender), per_page=5))
        for record in records:
            record["query"] = f"live:{profile_id}"
        db.insert_items(records)
        with db.db_connection() as connection:
            pending = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM items WHERE query=? AND tags_json IS NULL",
                    (f"live:{profile_id}",),
                ).fetchall()
            ]
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(tag_item, item): item for item in pending}
            for future, item in futures.items():
                try:
                    tags, meta = future.result()
                    db.save_tags(item["id"], tags.model_dump(), meta.request_id, item["image_path"])
                except Exception as exc:
                    db.save_tag_error(item["id"], str(exc))
        job.update(status="complete", fetched=len(records), tagged=len(pending))
    except Exception as exc:
        job.update(status="failed", error=str(exc))


@router.post("/api/profile")
def create_profile(body: ProfileBrief, background_tasks: BackgroundTasks) -> dict[str, Any]:
    profile_id = uuid.uuid4().hex
    brief = body.model_dump(exclude_none=True)
    try:
        taste, meta = create_taste(brief, profile_id)
    except TaskError as exc:
        raise _http_task_error(exc) from exc
    db.create_profile(profile_id, brief)
    job_id = uuid.uuid4().hex
    FETCH_JOBS[job_id] = {"id": job_id, "status": "queued", "fetched": 0, "tagged": 0}
    FETCH_JOBS[profile_id] = FETCH_JOBS[job_id]
    background_tasks.add_task(
        _fetch_profile_items,
        profile_id,
        taste.get("search_queries", []),
        brief["gender"],
    )
    return {
        "profile_id": profile_id,
        "brief": brief,
        "taste": taste,
        "version": 1,
        "request_id": meta.request_id,
        "fetch_job_id": job_id,
    }


@router.get("/api/fetch/{job_id}")
def fetch_job(job_id: str) -> dict[str, Any]:
    job = FETCH_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Fetch job not found")
    return dict(job)


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
    ranked = rank_items(candidates, active["brief"], taste, exclude_ids=swiped)
    return [_public_item(item) for item in make_deck(ranked, n)]


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
        "signal_counts": counts,
    }


@router.post("/api/outfits")
def outfits(body: OutfitRequest) -> dict[str, Any]:
    active = _user_profile()
    current = db.latest_taste(active["id"])
    profile = current["profile"]
    items = db.list_items(limit=5000)
    pool = build_pool(items, active["brief"], profile, body.locked_ids, body.exclude_ids)
    if not pool:
        raise HTTPException(status_code=409, detail="No eligible tagged items")
    content = stylist_user_content(profile, body.count, body.locked_ids, pool)
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
        saved.append({"id": outfit_id, **outfit})
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
        item for item in rank_items(items, active["brief"], current)
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
    updated["pieces"] = [db.get_item_with_tags(item_id) for item_id in ids]
    return {"outfit": updated, "total_gbp": round(total, 2)}


@router.post("/api/outfits/{outfit_id}/verdict")
def verdict(outfit_id: str, body: VerdictRequest) -> dict[str, Any]:
    active = _user_profile()
    outfit = db.get_outfit(outfit_id)
    if not outfit or outfit["profile_id"] != active["id"]:
        raise HTTPException(status_code=404, detail="Outfit not found")
    saved = db.update_outfit_verdict(outfit_id, body.verdict, body.reason)
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
        "outfit": saved,
        "taste": {"version": version, "profile": profile, "changes": profile["changes"]},
        "request_id": meta.request_id,
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
    ranked = rank_items(matches, active["brief"], current)
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
        upload_id = db.add_upload(active["id"], str(path), read.model_dump(), meta.request_id)
        version, profile, taste_meta, _ = update_taste(active)
        request_ids.append(taste_meta.request_id)
    except TaskError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "error": str(exc),
                "request_ids": [*request_ids, *exc.request_ids],
            },
        ) from exc
    return {
        "upload_id": upload_id, "read": read.model_dump(),
        "taste": {"version": version, "profile": profile},
        "request_ids": request_ids,
    }


@router.post("/api/search/live")
def live_search(body: LiveSearchRequest) -> dict[str, Any]:
    active = _user_profile()
    started = monotonic()
    gender = _gender_code(active["brief"]["gender"])
    items = vinted.search(body.q, gender, per_page=10)
    retail_future = None
    if monotonic() - started < 20:
        retail_future = LIVE_RETAIL_EXECUTOR.submit(retail.search, body.q, 10, gender)
    retail_items = []
    if retail_future:
        try:
            retail_items = retail_future.result(timeout=max(0.01, 20 - (monotonic() - started)))
        except TimeoutError:
            retail_future.cancel()
        except Exception:
            retail_items = []
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
                    "AND tags_json IS NULL",
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
    }


@router.post("/api/reset")
def reset() -> dict[str, Any]:
    with db.db_connection() as connection:
        connection.execute(
            "DELETE FROM swipes WHERE profile_id IN (SELECT id FROM profiles WHERE kind='user')"
        )
        for table in ("taste_versions", "outfits", "corrections", "uploads"):
            connection.execute(
                f"DELETE FROM {table} "
                "WHERE profile_id IN (SELECT id FROM profiles WHERE kind='user')"
            )
        connection.execute("DELETE FROM profiles WHERE kind='user'")
        connection.execute("DELETE FROM items WHERE query LIKE 'live:%'")
    FETCH_JOBS.clear()
    return {"ok": True}
