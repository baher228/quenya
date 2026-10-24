import json
from typing import Any

from app.rank import rank_items, tags_for

SLOT_QUOTAS = {
    "outerwear": 6,
    "top": 8,
    "knitwear": 6,
    "bottom": 8,
    "dress": 6,
    "shoes": 8,
    "bag": 4,
    "accessory": 4,
}


def build_pool(
    items: list[dict[str, Any]],
    brief: dict[str, Any],
    profile: dict[str, Any],
    locked_ids: list[str],
    exclude_ids: list[str],
    *,
    core_profile: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    ranked = rank_items(
        items, brief, profile, exclude_ids=set(exclude_ids), core_profile=core_profile
    )
    by_slot: dict[str, list[dict[str, Any]]] = {}
    for item in ranked:
        by_slot.setdefault(str(tags_for(item).get("slot")), []).append(item)
    pool: dict[str, dict[str, Any]] = {}
    for slot, quota in SLOT_QUOTAS.items():
        if slot == "shoes" and brief.get("include_shoes") is False:
            continue
        if slot == "dress" and brief.get("gender") == "menswear":
            continue
        for item in by_slot.get(slot, [])[:quota]:
            pool[item["id"]] = item
    for item_id in locked_ids:
        item = next((item for item in items if item["id"] == item_id), None)
        if item and tags_for(item):
            pool[item_id] = item
    return list(pool.values())


def stylist_user_content(
    profile: dict[str, Any],
    count: int,
    locked_ids: list[str],
    pool: list[dict[str, Any]],
    *,
    core_rules: list[str] | None = None,
    history: dict[str, Any] | None = None,
    include_shoes: bool = True,
    style_notes: list[str] | None = None,
) -> str:
    lines = [
        f"{item['id']} | {tags_for(item).get('slot')} | "
        f"£{float(item['price_gbp']):.0f} | {tags_for(item).get('category')} | "
        f"{', '.join(tags_for(item).get('colours', []))} | "
        f"{', '.join(tags_for(item).get('aesthetics', []))} | "
        f"niche {float(tags_for(item).get('niche', 0)):.1f} | "
        f"{tags_for(item).get('fit')} | {item['source']}"
        for item in pool
    ]
    request = (
        "Request: clothes only, no shoes."
        if not include_shoes
        else f"Request: build {count} outfits. Locked piece ids: {json.dumps(locked_ids)}."
    )
    profile_for_stylist = {
        key: value for key, value in profile.items() if key != "changes"
    }
    content = (
        f"Profile:\n{json.dumps(profile_for_stylist, ensure_ascii=False, separators=(',', ':'))}"
    )
    if history is not None:
        history_line = (
            "Overall history for this style (all swipes): "
            f"kept {history.get('kept', 0)}, passed {history.get('passed', 0)}."
        )
        most_kept = []
        for key in ("aesthetics", "categories", "colours"):
            entries = history.get(key) or []
            if entries:
                values = ", ".join(f"{name} {count}" for name, count in entries[:5])
                most_kept.append(f"{key} {values}")
        if most_kept:
            history_line += " Most kept: " + "; ".join(most_kept)
        content += "\n" + history_line
    if style_notes:
        content += "\nStyle notes: " + "; ".join(style_notes)
    content += f"\n\n{request}\n\nPool:\n" + "\n".join(lines)
    if core_rules:
        content += (
            "\nShared rules across the shopper's boards: "
            + "; ".join(core_rules)
        )
    return content


def validate_outfits(
    outfits: list[dict[str, Any]],
    pool: list[dict[str, Any]],
    count: int,
    locked_ids: list[str],
    price_ceiling: float,
    *,
    include_shoes: bool = True,
) -> tuple[list[dict[str, Any]], list[str]]:
    by_id = {item["id"]: item for item in pool}
    valid: list[dict[str, Any]] = []
    errors: list[str] = []
    used: set[str] = set()
    if len(outfits) != count:
        errors.append(f"expected {count} outfits, got {len(outfits)}")
    for index, outfit in enumerate(outfits):
        piece_ids = outfit["piece_ids"]
        pieces = [by_id.get(item_id) for item_id in piece_ids]
        reasons = []
        if index >= count:
            reasons.append("unexpected extra outfit")
        if not 3 <= len(piece_ids) <= 5:
            reasons.append("must contain 3-5 pieces")
        if len(piece_ids) != len(set(piece_ids)) or any(piece is None for piece in pieces):
            reasons.append("contains duplicate ids or ids outside the pool")
        if not set(locked_ids).issubset(piece_ids):
            reasons.append("does not include all locked ids")
        existing = [piece for piece in pieces if piece]
        slots = [tags_for(piece).get("slot") for piece in existing]
        if any(slots.count(slot) > (2 if slot == "accessory" else 1) for slot in set(slots)):
            reasons.append("breaks slot limits")
        complete_clothing = "dress" in slots or (
            {"top", "knitwear"} & set(slots) and "bottom" in slots
        )
        if include_shoes:
            if "shoes" not in slots or not complete_clothing:
                reasons.append("missing shoes or a complete clothing combination")
        elif "shoes" in slots or not complete_clothing:
            reasons.append("must be shoe-less and contain a complete clothing combination")
        total = sum(float(piece["price_gbp"] or 0) for piece in existing)
        if total > price_ceiling:
            reasons.append("exceeds the price ceiling")
        reused = (set(piece_ids) - set(locked_ids)) & used
        if reused:
            reasons.append(f"reuses non-locked ids: {sorted(reused)}")
        if reasons:
            errors.append(f"outfit {index + 1}: {', '.join(reasons)}")
            continue
        used.update(set(piece_ids) - set(locked_ids))
        valid.append(
            {
                **outfit,
                "piece_ids": piece_ids,
                "pieces": existing,
                "total_gbp": round(total, 2),
            }
        )
    return valid, errors
