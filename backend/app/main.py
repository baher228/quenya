import json
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from app import db
from app.config import (
    MUBIT_API_KEY,
    PROMPT_VERSION,
    TAVILY_API_KEY,
)
from app.images import cached_image_path, image_key
from app.mubit import TaskError
from app.prompts import READER_MODEL, STYLIST_MODEL, TAGGER_MODEL, TASTE_MODEL
from app.tagging import tag_item

app = FastAPI(title="Quenya API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class CorrectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str = Field(min_length=1, max_length=1000)


@app.on_event("startup")
def startup() -> None:
    db.init_db()


@app.get("/api/health")
def health() -> dict:
    total, tagged = db.total_counts()
    return {
        "ok": True,
        "mubit_key_present": bool(MUBIT_API_KEY),
        "tavily_key_present": bool(TAVILY_API_KEY),
        "models": {
            "tagger": TAGGER_MODEL,
            "taste": TASTE_MODEL,
            "stylist": STYLIST_MODEL,
            "reader": READER_MODEL,
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
        item["image_url"] = f"/img/{image_key(item['image_url'])}.jpg"
        result.append(item)
    return result


@app.post("/api/items/{item_id}/correct-tags")
def correct_tags(item_id: str, body: CorrectionRequest) -> dict:
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
    with db.db_connection() as connection:
        connection.execute(
            """INSERT INTO corrections
               (id, profile_id, kind, target_id, before_json, after_json,
                note, request_id, created_at)
               VALUES (lower(hex(randomblob(16))), 'default', 'tag', ?, ?, ?, ?, ?, ?)""",
            (
                item_id,
                before or json.dumps({}),
                json.dumps(tags_json, ensure_ascii=False),
                body.note,
                meta.request_id,
                db.now_iso(),
            ),
        )
    return {"tags": tags_json, "request_id": meta.request_id}


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
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(path, media_type="image/jpeg")
