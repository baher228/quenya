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
) -> list[dict[str, Any]]:
    ranked = rank_items(items, brief, profile, exclude_ids=set(exclude_ids))
    by_slot: dict[str, list[dict[str, Any]]] = {}
    for item in ranked:
        by_slot.setdefault(str(tags_for(item).get("slot")), []).append(item)
    pool: dict[str, dict[str, Any]] = {}
    for slot, quota in SLOT_QUOTAS.items():
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
    profile: dict[str, Any], count: int, locked_ids: list[str], pool: list[dict[str, Any]]
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
    return (
        f"Profile:\n{json.dumps(profile, ensure_ascii=False, separators=(',', ':'))}\n\n"
        f"Request: build {count} outfits. Locked piece ids: "
        f"{json.dumps(locked_ids)}.\n\nPool:\n" + "\n".join(lines)
    )


def validate_outfits(
    outfits: list[dict[str, Any]],
    pool: list[dict[str, Any]],
    count: int,
    locked_ids: list[str],
    price_ceiling: float,
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
        if "shoes" not in slots or not (
            "dress" in slots or ({"top", "knitwear"} & set(slots) and "bottom" in slots)
        ):
            reasons.append("missing shoes or a complete clothing combination")
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
