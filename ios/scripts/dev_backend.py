#!/usr/bin/env python3
"""DEV ONLY: local API server with deterministic, fake LLM responses."""

from __future__ import annotations

import json
import os
import re
import sys
import uuid
from pathlib import Path

os.environ.setdefault("QUENYA_RANKER", "tags")
os.environ.setdefault("QUENYA_TAGGER", "llm")

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))

import uvicorn

from app import db, images, phase2_api, routing, styles, tagging, taste
from app.llm import CallMeta
from app.main import app
from app.schemas import InspirationGarment, ItemTags, RouteOut, TasteProfile


def _meta(task: str) -> CallMeta:
    return CallMeta(
        request_id=f"local-{task}-{uuid.uuid4().hex[:10]}",
        model_returned="local-fake",
        latency_ms=1,
        prompt_tokens=1,
        completion_tokens=1,
    )


def _active_profile() -> dict:
    try:
        return phase2_api._user_profile()
    except Exception:
        profile = db.active_profile("user")
        if not profile:
            raise RuntimeError("The fake taste task needs an active local profile.")
        return profile


def _taste_profile(content: object) -> TasteProfile:
    text = _prompt_text(content)
    match = re.search(r"^Brief:\n([^\n]+)", text, re.MULTILINE)
    brief = json.loads(match.group(1)) if match else {}
    try:
        active = _active_profile()
    except RuntimeError:
        active = None
    if active:
        brief = active["brief"]
    likes: dict[str, float] = {}
    avoids: dict[str, float] = {}
    for swipe in db.profile_swipes(active["id"]) if active else []:
        item = db.get_item_with_tags(swipe["item_id"])
        if not item:
            continue
        tags = item.get("tags") or {}
        destination = likes if swipe["liked"] else avoids
        keys = [
            *tags.get("aesthetics", []),
            tags.get("category"),
            *tags.get("colours", []),
        ]
        for key in keys:
            if key:
                destination[str(key)] = min(1.0, destination.get(str(key), 0.0) + 0.25)
    if not likes:
        likes = {"workwear": 0.5, "relaxed fits": 0.35}
    return TasteProfile.model_validate(
        {
            "summary": "A practical, considered wardrobe with room for character.",
            "likes": likes,
            "avoids": avoids,
            "rules": ["Keep the look wearable", "Prefer considered materials"],
            "palette": ["olive", "cream", "brown"],
            "niche_level": float(brief.get("niche", 0.5)),
            "price_ceiling_gbp": float(brief.get("budget_gbp", 300)),
            "condition_preference": brief.get("condition", "both"),
            "search_queries": [
                f"{brief.get('gender', 'menswear')} vintage workwear",
                str(brief.get("text", "considered wardrobe"))[:80],
            ],
            "changes": ["Your style is taking shape from your brief and feedback."],
        }
    )


def _prompt_text(content: object) -> str:
    if isinstance(content, list):
        return "\n".join(
            str(message.get("content", ""))
            for message in content
            if isinstance(message, dict)
        )
    return str(content)


def _stylist_output(content: object, out_model: type) -> object:
    text = _prompt_text(content)
    pool_text = text.split("\nPool:\n", 1)[-1]
    pool_text = pool_text.split("\nShared rules", 1)[0]
    candidates = []
    for line in pool_text.splitlines():
        fields = [field.strip() for field in line.split(" | ")]
        if len(fields) >= 3 and fields[0]:
            candidates.append({"id": fields[0], "slot": fields[1], "role": fields[2]})

    locked: list[str] = []
    match = re.search(r"Locked piece ids:\s*(\[[^\]]*\])", text)
    if match:
        try:
            locked = json.loads(match.group(1))
        except json.JSONDecodeError:
            locked = []
    by_id = {item["id"]: item for item in candidates}
    bases = [item for item in candidates if item["role"] == "base"]
    bottoms = [item for item in candidates if item["slot"] == "bottom"]
    shoes = [item for item in candidates if item["slot"] == "shoes"]
    dresses = [item for item in candidates if item["slot"] == "dress"]
    include_shoes = "Request: clothes only, no shoes." not in text
    count_match = re.search(r"Request: build (\d+) outfits", text)
    count = int(count_match.group(1)) if count_match else 1
    outfits = []

    for index in range(count):
        chosen = [by_id[item_id] for item_id in locked if item_id in by_id]
        if not any(item["role"] == "base" for item in chosen):
            if bases:
                chosen.append(bases[index % len(bases)])
            elif dresses:
                chosen.append(dresses[index % len(dresses)])
        if not any(item["slot"] in {"bottom", "dress"} for item in chosen):
            if bottoms:
                chosen.append(bottoms[index % len(bottoms)])
        if include_shoes and not any(item["slot"] == "shoes" for item in chosen) and shoes:
            chosen.append(shoes[index % len(shoes)])
        ids = list(dict.fromkeys(item["id"] for item in chosen))
        outfits.append(
            {
                "title": f"Local look {index + 1}",
                "mood": "Easy workwear",
                "piece_ids": ids,
                "why": "A balanced combination selected from the local catalogue.",
            }
        )
    return out_model.model_validate({"outfits": outfits})


def _fake_call_task(task, _system, _model, content, out_model, **_kwargs):
    if task == "taste":
        result = _taste_profile(content)
    elif task == "stylist":
        result = _stylist_output(content, out_model)
    elif task == "router":
        result = RouteOut(
            context="city",
            board_id=None,
            label="Photo ideas",
            confidence=0.2,
            reason="The local fake leaves routing for the user to decide.",
        )
    elif task == "reader":
        result = out_model.model_validate(
            {
                "garments": [
                    InspirationGarment(
                        slot="outerwear",
                        category="jacket",
                        colours=["olive", "cream"],
                    ).model_dump()
                ],
                "aesthetics": ["workwear"],
                "palette": ["olive", "cream"],
                "fit": "regular",
                "era": "contemporary",
                "search_queries": ["olive workwear jacket"],
                "one_line": "A relaxed olive workwear look with a clean palette.",
            }
        )
    elif task == "tagger":
        result = ItemTags(
            slot="outerwear",
            category="jacket",
            colours=["olive"],
            materials=["cotton"],
            fit="regular",
            era="90s",
            aesthetics=["workwear"],
            formality=2,
            niche=0.5,
            gender="unisex",
            logo_heavy=False,
            condition="second-hand",
            one_line="A relaxed vintage workwear jacket.",
        )
    elif task == "decoder":
        result = out_model.model_validate({"styles": [], "unknown_terms": [], "confidence": 1})
    else:
        raise ValueError(f"Unsupported local fake task: {task}")
    return result, _meta(task)


def configure_fakes() -> None:
    images.IMAGE_DIR = Path("/tmp/quenya-img")
    for module in (phase2_api, routing, styles, taste, tagging):
        module.call_task = _fake_call_task
    phase2_api.decode_brief = lambda _brief: []
    phase2_api.update_taste = lambda profile, **_kwargs: taste.update_taste(
        profile, require_signals=False
    )
    phase2_api._schedule_vinted_refill = lambda *_args, **_kwargs: None
    phase2_api._start_fetch = lambda *_args, **_kwargs: None


if __name__ == "__main__":
    configure_fakes()
    print("DEV ONLY: Quenya API using deterministic fake LLM tasks on :8000.")
    uvicorn.run(app, host="0.0.0.0", port=8000)
