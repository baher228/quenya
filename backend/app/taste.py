import json
from typing import Any

from app import db
from app.mubit import CallMeta, call_task
from app.prompts import TASTE_MODEL, TASTE_SYSTEM
from app.schemas import TasteProfile


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _signal_lines(
    signals: dict[str, list[dict[str, Any]]]
) -> tuple[list[str], dict[str, list[str]]]:
    lines: list[str] = []
    consumed: dict[str, list[str]] = {
        "swipes": [],
        "outfits": [],
        "corrections": [],
        "uploads": [],
    }
    for swipe in signals["swipes"]:
        tags = swipe["tags"]
        label = "kept" if swipe["liked"] else "passed"
        lines.append(
            f"- {label}: {swipe['title']} | {tags.get('category', '')} | "
            f"{', '.join(tags.get('aesthetics', []))} | "
            f"{', '.join(tags.get('colours', []))} | niche {float(tags.get('niche', 0)):.1f} | "
            f"£{float(swipe['price_gbp'] or 0):.0f}"
        )
        consumed["swipes"].append(swipe["id"])
    for outfit in signals["outfits"]:
        pieces = [
            db.get_item_with_tags(item_id)
            for item_id in json.loads(outfit["piece_ids_json"])
        ]
        categories = [
            (item.get("tags") or {}).get("category", item["title"])
            for item in pieces
            if item
        ]
        action = "liked" if outfit["verdict"] == "like" else "rejected"
        line = f'- outfit {action} "{outfit["title"]}": {" + ".join(categories)}'
        if outfit.get("reason"):
            line += f' | reason: "{outfit["reason"]}"'
        lines.append("- " + line[2:])
        consumed["outfits"].append(outfit["id"])
    for correction in signals["corrections"]:
        lines.append(
            f'- tag correction on "{correction.get("title") or correction["target_id"]}": '
            f'"{correction["note"]}"'
        )
        consumed["corrections"].append(correction["id"])
    for upload in signals["uploads"]:
        read = upload["read"]
        lines.append(
            f'- inspiration photo: {read.get("one_line", "")} | '
            f'{", ".join(read.get("aesthetics", []))} | '
            f'palette {", ".join(read.get("palette", []))}'
        )
        consumed["uploads"].append(upload["id"])
    return lines, consumed


def taste_user_content(
    brief: dict[str, Any],
    current: dict[str, Any] | None,
    version: int,
    lines: list[str],
) -> str:
    sections = [
        f"Brief:\n{_compact(brief)}",
        f"Current profile (v{version}):\n{_compact(current) if current else 'none'}",
    ]
    if lines:
        sections.append("New signals:\n" + "\n".join(lines))
    return "\n\n".join(sections)


def normalize_profile(profile: TasteProfile, brief: dict[str, Any]) -> dict[str, Any]:
    data = profile.model_dump()
    for field in ("likes", "avoids"):
        weights = {
            key: max(0.0, min(1.0, float(value)))
            for key, value in data[field].items()
        }
        data[field] = dict(
            sorted(weights.items(), key=lambda entry: entry[1], reverse=True)[:15]
        )
    if data["price_ceiling_gbp"] <= 0:
        data["price_ceiling_gbp"] = float(brief["budget_gbp"])
    if brief["condition"] != "both":
        data["condition_preference"] = brief["condition"]
    return data


def create_taste(
    brief: dict[str, Any], profile_id: str
) -> tuple[dict[str, Any], CallMeta]:
    content = taste_user_content(brief, None, 1, [])
    result, meta = call_task("taste", TASTE_SYSTEM, TASTE_MODEL, content, TasteProfile)
    profile = normalize_profile(result, brief)
    db.save_taste_version(
        profile_id,
        1,
        profile,
        {"swipes": [], "outfits": [], "corrections": [], "uploads": []},
        meta.request_id,
    )
    return profile, meta


def update_taste(
    profile_row: dict[str, Any], *, require_signals: bool = True
) -> tuple[int, dict[str, Any], CallMeta, dict[str, Any]]:
    profile_id = profile_row["id"]
    current_row = db.latest_taste(profile_id)
    current_version = int(current_row["version"]) if current_row else 0
    version = current_version + 1
    current = current_row["profile"] if current_row else None
    signals = db.unconsumed_signals(profile_id)
    lines, consumed = _signal_lines(signals)
    if require_signals and not lines:
        raise ValueError("No unconsumed taste signals")
    content = taste_user_content(profile_row["brief"], current, current_version, lines)
    result, meta = call_task("taste", TASTE_SYSTEM, TASTE_MODEL, content, TasteProfile)
    profile = normalize_profile(result, profile_row["brief"])
    stored_signals = {
        "lines": lines,
        "consumed_ids": consumed,
    }
    db.save_taste_version(
        profile_id, version, profile, stored_signals, meta.request_id
    )
    return version, profile, meta, stored_signals
