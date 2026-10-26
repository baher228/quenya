import json
import re
from typing import Any, Literal

from app.rank import rank_items, tags_for

_LAYER_SLOTS = {"top", "knitwear", "outerwear"}
_LAYER_ROLE_QUOTAS = {"base": 10, "mid": 8, "outer": 6}
_NON_OUTFIT_BASE = re.compile(
    r"\b(?:sports? bra|bra|bralette|bikini|swim\w*|wetsuit|yulex|rash ?guard|"
    r"pyjamas?|pajamas?|loungewear|lounge (?:top|set|pant|trouser)s?|"
    r"nightie|nightdress|briefs|boxers?|underwear)\b",
    re.I,
)
_OUTER_LAYER = re.compile(
    r"\b(?:coats?|jackets?|parkas?|puffers?|anoraks?|blazers?|gilets?|"
    r"windbreakers?|raincoats?|shackets?|trenches|trench|cagoules?|"
    r"ponchos?|mac|suits?)\b",
    re.I,
)
_MID_LAYER = re.compile(
    r"\b(?:hoodies?|hoody|sweatshirts?|sweat|jumpers?|sweaters?|cardigans?|"
    r"fleeces?|quarter[\s-]+zip|half[\s-]+zip|1/4\s+zip|zip[- ]neck|"
    r"pullovers?|overshirts?|waistcoats?|track\s+top|knit\s+sweatshirts?)\b",
    re.I,
)
_BASE_LAYER = re.compile(
    r"\b(?:t[- ]?shirts?|tees?|tanks?|vests?|polos?|shirts?|blouses?|tops?|"
    r"camisoles?|camis?|bodysuits?|bras?|henleys?|long[\s-]+sleeve|crops?)\b",
    re.I,
)
_WARM_SHORTS = re.compile(r"\bshorts?\b", re.I)
_WARM_TOP = re.compile(
    r"\b(?:tanks?(?:\s+tops?)?|vests?(?:\s+tops?)?|camisoles?|camis?|strappy)\b",
    re.I,
)
_WARM_SHOES = re.compile(
    r"\b(?:sandals?|slides?|flip[\s-]?flops?|espadrilles?)\b", re.I
)
_COLD_OUTER = re.compile(
    r"\b(?:coats?|overcoats?|parkas?|puffers?|padded|quilted|shearlings?|"
    r"peacoats?|duffles?|trenches?|sheepskin)\b|"
    r"(?<!button[- ])\bdown\s+(?:jacket|coat|parka|vest)\b",
    re.I,
)
_COLD_ACCESSORY = re.compile(
    r"\b(?:scarf|scarves|scarfs?|gloves?|mittens?|beanie|beanies|"
    r"balaclavas?|earmuffs?)\b",
    re.I,
)
_BASE_TEE = re.compile(r"\b(?:t[\s-]?shirts?|tees?)\b", re.I)
_BASE_POLO = re.compile(r"\bpolos?\b", re.I)
_BASE_SHIRT = re.compile(r"\bshirts?\b", re.I)

_CANONICAL_ROLE_ORDER = {"outer": 0, "mid": 1, "base": 2}
_CANONICAL_SLOT_ORDER = {
    "dress": 3,
    "bottom": 4,
    "shoes": 5,
    "bag": 6,
    "accessory": 7,
}

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


def layer_role(item: dict[str, Any]) -> Literal["base", "mid", "outer"] | None:
    tags = tags_for(item)
    slot = str(tags.get("slot") or "").strip().lower()
    if slot not in _LAYER_SLOTS:
        return None
    category = str(tags.get("category") or "").strip()
    text = (category or str(item.get("title") or "")).lower()
    if _OUTER_LAYER.search(text):
        return "outer"
    if _MID_LAYER.search(text):
        return "mid"
    if _BASE_LAYER.search(text):
        return "base"
    return {"outerwear": "outer", "knitwear": "mid", "top": "base"}[slot]


def _item_texts(item: dict[str, Any]) -> list[str]:
    values = (tags_for(item).get("category"), item.get("title"))
    return [str(value) for value in values if value]


def is_outfit_eligible(item: dict[str, Any]) -> bool:
    return not any(_NON_OUTFIT_BASE.search(text) for text in _item_texts(item))


def warmth(item: dict[str, Any]) -> Literal["warm", "cold"] | None:
    tags = tags_for(item)
    slot = str(tags.get("slot") or "").strip().lower()
    texts = _item_texts(item)
    if slot == "bottom" and any(_WARM_SHORTS.search(text) for text in texts):
        return "warm"
    if (
        slot == "top"
        and layer_role(item) == "base"
        and any(_WARM_TOP.search(text) for text in texts)
    ):
        return "warm"
    if slot == "shoes" and any(_WARM_SHOES.search(text) for text in texts):
        return "warm"
    if (
        slot in _LAYER_SLOTS
        and (slot == "outerwear" or layer_role(item) == "outer")
        and any(_COLD_OUTER.search(text) for text in texts)
    ):
        return "cold"
    if slot == "accessory" and any(
        _COLD_ACCESSORY.search(text) for text in texts
    ):
        return "cold"
    return None


def base_layer_subtype(item: dict[str, Any]) -> Literal["tee", "shirt", "polo", "other"]:
    text = " ".join(_item_texts(item)).strip().lower()
    if _BASE_TEE.search(text):
        return "tee"
    if _BASE_POLO.search(text):
        return "polo"
    if _BASE_SHIRT.search(text):
        return "shirt"
    return "other"


def order_outfit_pieces(pieces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def order(item: dict[str, Any]) -> int:
        role = layer_role(item)
        if role is not None:
            return _CANONICAL_ROLE_ORDER[role]
        slot = str(tags_for(item).get("slot") or "").strip().lower()
        return _CANONICAL_SLOT_ORDER.get(slot, 8)

    return sorted(pieces, key=order)


def build_pool(
    items: list[dict[str, Any]],
    brief: dict[str, Any],
    profile: dict[str, Any],
    locked_ids: list[str],
    exclude_ids: list[str],
    *,
    core_profile: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    ranked = [
        item
        for item in rank_items(
            items,
            brief,
            profile,
            exclude_ids=set(exclude_ids),
            core_profile=core_profile,
        )
        if is_outfit_eligible(item)
    ]
    by_slot: dict[str, list[dict[str, Any]]] = {}
    by_role: dict[str, list[dict[str, Any]]] = {}
    for item in ranked:
        slot = str(tags_for(item).get("slot"))
        if slot in _LAYER_SLOTS:
            role = layer_role(item)
            if role:
                by_role.setdefault(role, []).append(item)
        else:
            by_slot.setdefault(slot, []).append(item)
    pool: dict[str, dict[str, Any]] = {}
    for slot, quota in SLOT_QUOTAS.items():
        if slot in _LAYER_SLOTS:
            continue
        if slot == "shoes" and brief.get("include_shoes") is False:
            continue
        if slot == "dress" and brief.get("gender") == "menswear":
            continue
        for item in by_slot.get(slot, [])[:quota]:
            pool[item["id"]] = item
    for role, quota in _LAYER_ROLE_QUOTAS.items():
        role_items = by_role.get(role, [])
        if role == "base":
            selected = [
                item
                for item in role_items
                if base_layer_subtype(item) == "tee"
            ][:5]
            selected_ids = {item["id"] for item in selected}
            selected.extend(
                item
                for item in role_items
                if item["id"] not in selected_ids
            )
            role_items = selected
        for item in role_items[:quota]:
            pool[item["id"]] = item
    for item_id in locked_ids:
        item = next((item for item in items if item["id"] == item_id), None)
        if item and tags_for(item) and is_outfit_eligible(item):
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
        f"{layer_role(item) or '-'} | {warmth(item) or '-'} | "
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


def outfit_errors(
    pieces: list[dict[str, Any]], include_shoes: bool
) -> list[str]:
    errors = []
    slots = [tags_for(piece).get("slot") for piece in pieces]
    dress_count = slots.count("dress")
    bottom_count = slots.count("bottom")
    if dress_count > 1:
        errors.append("more than one dress")
    layer_counts = {
        role: sum(layer_role(piece) == role for piece in pieces)
        for role in ("base", "mid", "outer")
    }
    if dress_count:
        if layer_counts["base"]:
            errors.append("a dress outfit cannot include a base layer")
        if bottom_count:
            errors.append("a dress outfit cannot include a bottom")
    else:
        if layer_counts["base"] != 1:
            errors.append("needs exactly one base layer")
        if bottom_count != 1:
            errors.append("needs exactly one bottom")
    if layer_counts["mid"] > 1:
        errors.append(
            "two mid layers"
            if layer_counts["mid"] == 2
            else "more than two mid layers"
        )
    if layer_counts["outer"] > 1:
        errors.append(
            "two outer layers"
            if layer_counts["outer"] == 2
            else "more than two outer layers"
        )
    clothing_count = sum(
        slot not in {"shoes", "bag", "accessory"} for slot in slots
    )
    if clothing_count > 4:
        errors.append("more than 4 clothing pieces")
    shoe_count = slots.count("shoes")
    if include_shoes and shoe_count != 1:
        errors.append("needs exactly one pair of shoes")
    elif not include_shoes and shoe_count:
        errors.append("shoes are not allowed")
    bag_count = slots.count("bag")
    accessory_count = slots.count("accessory")
    if bag_count > 1:
        errors.append("more than one bag")
    if accessory_count > 2:
        errors.append("more than two accessories")
    if bag_count + accessory_count > 2:
        errors.append("more than 2 bag/accessory pieces")
    if any(warmth(piece) == "warm" for piece in pieces) and any(
        warmth(piece) == "cold" for piece in pieces
    ):
        errors.append(
            "warm-weather piece with cold-weather piece "
            "(e.g. shorts with a heavy coat)"
        )
    return errors


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
        if len(piece_ids) != len(set(piece_ids)) or any(piece is None for piece in pieces):
            reasons.append("contains duplicate ids or ids outside the pool")
        if not set(locked_ids).issubset(piece_ids):
            reasons.append("does not include all locked ids")
        existing = [piece for piece in pieces if piece]
        reasons.extend(outfit_errors(existing, include_shoes))
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
