import json
import logging
from threading import Thread
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import Request

from app import db
from app.config import (
    CURSOR_API_KEY,
    PROMPT_VERSION,
    QUENYA_CORS_ORIGINS,
    QUENYA_LLM_API_KEY,
    QUENYA_LLM_PROVIDER,
    QUENYA_WARM_CACHES,
    TAVILY_API_KEY,
)
from app.images import (
    cached_image_path,
    r2_enabled,
    r2_public_url,
    storage_enabled,
    storage_public_url,
)
from app.llm import TaskError
from app.phase2_api import _public_item
from app.phase2_api import router as phase2_router
from app.prompts import (
    DECODER_MODEL,
    READER_MODEL,
    ROUTER_MODEL,
    STYLIST_MODEL,
    TAGGER_MODEL,
    TASTE_MODEL,
)
from app.tagging import tag_item
from app.taste import update_taste
from app.vinted_proxy import router as vinted_proxy_router

app = FastAPI(title="Quenya API")
logger = logging.getLogger(__name__)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in QUENYA_CORS_ORIGINS.split(",")
        if origin.strip()
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(HTTPException)
async def http_error_handler(_request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if not isinstance(detail, dict) or "error" not in detail:
        detail = {"error": str(detail), "request_ids": []}
    else:
        detail = {
            "error": detail["error"],
            "request_ids": detail.get("request_ids", []),
        }
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": detail},
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    _request: Request, _exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"detail": {"error": "Invalid request", "request_ids": []}},
    )


class CorrectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str = Field(min_length=1, max_length=1000)


@app.on_event("startup")
def startup() -> None:
    db.init_db()
    if QUENYA_WARM_CACHES:
        Thread(
            target=_warm_caches,
            daemon=True,
            name="cache-warmup",
        ).start()


def _warm_caches() -> None:
    from app.embedding_ranker import catalogue_sample

    for cache_name, warm in (
        ("live catalogue", db.live_catalogue),
        ("embedding catalogue sample", catalogue_sample),
    ):
        try:
            warm()
        except Exception:
            logger.exception("Failed to warm %s cache", cache_name)


@app.get("/health", include_in_schema=False)
@app.get("/api/health")
def health() -> dict:
    total, tagged = db.total_counts()
    provider = QUENYA_LLM_PROVIDER.strip().lower()
    llm_ready = (
        bool(CURSOR_API_KEY) if provider == "cursor"
        else bool(QUENYA_LLM_API_KEY) if provider == "openai"
        else False
    )
    return {
        "ok": True,
        "llm_provider": provider,
        "llm_ready": llm_ready,
        "tavily_key_present": bool(TAVILY_API_KEY),
        "models": {
            "tagger": TAGGER_MODEL,
            "taste": TASTE_MODEL,
            "stylist": STYLIST_MODEL,
            "reader": READER_MODEL,
            "router": ROUTER_MODEL,
            "decoder": DECODER_MODEL,
        },
        "prompt_version": PROMPT_VERSION,
        "items_total": total,
        "items_tagged": tagged,
    }


@app.get("/api/items")
def items(
    slot: str | None = None,
    gender: Literal["m", "w", "u"] | None = None,
    condition: Literal["second-hand", "new"] | None = None,
    source: Literal["vinted", "retail"] | None = None,
    limit: int = Query(default=50, ge=1, le=500),
) -> list[dict]:
    result = []
    for item in db.list_items(
        slot=slot, gender=gender, condition=condition, source=source, limit=limit
    ):
        result.append(_public_item(item))
    return result


@app.post("/api/items/{item_id}/correct-tags")
def correct_tags(item_id: str, body: CorrectionRequest) -> dict:
    profile = db.active_profile("user")
    if not profile:
        raise HTTPException(status_code=409, detail="Create a profile first")
    item = db.get_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    before = item.get("tags_json")
    try:
        tags, meta = tag_item(item, body.note)
    except TaskError as exc:
        raise HTTPException(
            status_code=502,
            detail={"error": str(exc), "request_ids": exc.request_ids},
        ) from exc
    tags_json = tags.model_dump()
    db.save_tags(item_id, tags_json, meta.request_id, item.get("image_path"))
    db.add_correction(
        profile["id"],
        "tag",
        item_id,
        json.loads(before) if before else {},
        tags_json,
        body.note,
        meta.request_id,
    )
    try:
        version, taste, taste_meta, _ = update_taste(profile)
    except TaskError as exc:
        raise HTTPException(
            status_code=502,
            detail={"error": str(exc), "request_ids": exc.request_ids},
        ) from exc
    return {
        "tags": tags_json,
        "request_id": meta.request_id,
        "taste": {
            "version": version,
            "profile": taste,
            "changes": taste["changes"],
            "request_id": taste_meta.request_id,
        },
    }


@app.get("/api/model/stats")
def model_stats() -> list[dict]:
    return db.model_stats()


@app.get("/img/{filename}")
def get_image(filename: str):
    if not filename.endswith(".jpg"):
        raise HTTPException(status_code=404, detail="Image not found")
    key = filename[:-4]
    try:
        path = cached_image_path(key)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Image not found") from exc
    if r2_enabled():
        return RedirectResponse(
            r2_public_url(f"{key}.jpg"),
            status_code=302,
        )
    if storage_enabled():
        return RedirectResponse(
            storage_public_url(f"{key}.jpg"),
            status_code=302,
        )
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(path, media_type="image/jpeg")


app.include_router(phase2_router)
app.include_router(vinted_proxy_router)
