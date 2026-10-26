import argparse
import json
import uuid
from pathlib import Path
from typing import Any

from app import db
from app.llm import call_task
from app.outfits import build_pool, stylist_user_content, validate_outfits
from app.prompts import STYLIST_MODEL, STYLIST_SYSTEM
from app.rank import make_deck, rank_items, tokens
from app.routing import route_input
from app.schemas import ProfileBrief, StylistOutput
from app.styles import style_notes
from app.taste import create_taste, update_taste

PERSONAS_PATH = Path(__file__).resolve().parents[1] / "seeds" / "personas.json"
ROUTER_PHRASES_PATH = Path(__file__).resolve().parents[1] / "seeds" / "router_phrases.txt"


def run_persona(persona: dict[str, Any], *, router: bool = False) -> dict[str, Any]:
    brief = ProfileBrief.model_validate(persona["brief"]).model_dump(exclude_none=True)
    if router:
        route_output, existing, route_meta, _ = route_input(
            "brief", brief["text"], kind="warmup"
        )
        profile_id = existing["id"] if existing else uuid.uuid4().hex
        route_id = db.create_route(
            "warmup",
            "brief",
            brief["text"],
            route_output.model_dump() | {"input_brief": brief},
            route_meta.request_id,
            profile_id if existing else None,
        )
        if existing:
            db.activate_profile(profile_id, kind="warmup")
            db.update_profile_brief(profile_id, brief)
            db.add_correction(
                profile_id,
                "brief",
                route_id,
                existing["brief"],
                brief,
                brief["text"],
                route_meta.request_id,
            )
        else:
            taste, _ = create_taste(brief, profile_id)
            db.create_profile(
                profile_id,
                brief,
                kind="warmup",
                active=False,
                label=route_output.label,
                context=route_output.context,
            )
    else:
        profile_id = uuid.uuid4().hex
        taste, _ = create_taste(brief, profile_id)
        db.create_profile(profile_id, brief, kind="warmup", active=False)
    profile_row = db.get_profile(profile_id, kind="warmup")
    snapshot_items = [
        item for item in db.list_items(limit=5000)
        if not (item.get("query") or "").startswith("live:")
    ]
    rounds = []
    keep_words = {word.lower() for word in persona["keep_words"]}
    for _ in range(3):
        taste = db.latest_taste(profile_id)["profile"]
        deck = make_deck(
            rank_items(snapshot_items, brief, taste),
            10,
            brief,
            include_shoes=brief.get("include_shoes", True),
        )
        for item in deck:
            liked = bool(tokens(item) & keep_words)
            db.insert_swipe(profile_id, item["id"], liked)
        version, taste, _, _ = update_taste(profile_row)
        pool = build_pool(snapshot_items, brief, taste, [], [])
        content = stylist_user_content(
            taste,
            3,
            [],
            pool,
            include_shoes=brief.get("include_shoes", True),
            style_notes=style_notes(brief),
        )
        result, meta = call_task("stylist", STYLIST_SYSTEM, STYLIST_MODEL, content, StylistOutput)
        valid, errors = validate_outfits(
            [outfit.model_dump() for outfit in result.outfits],
            pool,
            3,
            [],
            float(taste["price_ceiling_gbp"]),
            include_shoes=brief.get("include_shoes", True),
        )
        if errors:
            repair = [
                {"role": "user", "content": content},
                {"role": "assistant", "content": result.model_dump_json()},
                {
                    "role": "user",
                    "content": (
                        f"These outfits broke rules: {errors}. "
                        "Return all 3 outfits again, fixed."
                    ),
                },
            ]
            result, meta = call_task(
                "stylist", STYLIST_SYSTEM, STYLIST_MODEL, repair, StylistOutput
            )
            valid, _ = validate_outfits(
                [outfit.model_dump() for outfit in result.outfits],
                pool,
                3,
                [],
                float(taste["price_ceiling_gbp"]),
                include_shoes=brief.get("include_shoes", True),
            )
        saved_ids = []
        for outfit in valid:
            outfit_id = db.save_outfit(
                profile_id,
                version,
                outfit,
                outfit["piece_ids"],
                outfit["total_gbp"],
                meta.request_id,
            )
            saved_ids.append(outfit_id)
        for index, verdict in enumerate(("reject", "like")):
            if index >= len(saved_ids):
                break
            reason = persona["reject_reason"] if verdict == "reject" else None
            db.update_outfit_verdict(saved_ids[index], verdict, reason)
            if reason:
                db.add_correction(
                    profile_id,
                    "outfit",
                    saved_ids[index],
                    {},
                    {"verdict": verdict},
                    reason,
                    meta.request_id,
                )
            version, taste, _, _ = update_taste(profile_row)
        rounds.append({"version": version, "outfits": len(saved_ids)})
    return {"persona": persona["name"], "profile_id": profile_id, "rounds": rounds}


def run(personas: int | None = None, *, router: bool = False) -> list[dict[str, Any]]:
    db.init_db()
    rows = json.loads(PERSONAS_PATH.read_text())
    results = [run_persona(persona, router=router) for persona in rows[:personas]]
    if router:
        corrections = 0
        for line in ROUTER_PHRASES_PATH.read_text().splitlines():
            if not line or line.startswith("#"):
                continue
            expected, phrase = line.split("\t", 1)
            output, selected, meta, _ = route_input(
                "brief", phrase, kind="warmup"
            )
            route_id = db.create_route(
                "warmup",
                "brief",
                phrase,
                output.model_dump(),
                meta.request_id,
                selected["id"] if selected else None,
            )
            if output.context != expected and corrections < 6:
                db.correct_route(
                    route_id,
                    {
                        "board_id": selected["id"] if selected else None,
                        "context": expected,
                        "label": expected.title(),
                    },
                    kind="warmup",
                )
                corrections += 1
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--personas", type=int)
    parser.add_argument("--router", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.personas, router=args.router), indent=2))


if __name__ == "__main__":
    main()
