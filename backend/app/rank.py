import json
from typing import Any


def tags_for(item: dict[str, Any]) -> dict[str, Any]:
    tags = item.get("tags")
    if isinstance(tags, dict):
        return tags
    raw = item.get("tags_json")
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return {}


def tokens(item: dict[str, Any]) -> set[str]:
    tags = tags_for(item)
    values: list[Any] = []
    for key in ("aesthetics", "colours", "materials"):
        values.extend(tags.get(key) or [])
    values.extend((tags.get("category"), tags.get("fit"), tags.get("era")))
    return {
        str(value).strip().lower()
        for value in values
        if value and str(value).strip().lower() != "unknown"
    }


def score_item(item: dict[str, Any], profile: dict[str, Any]) -> float:
    item_tokens = tokens(item)
    likes = profile.get("likes") or {}
    avoids = profile.get("avoids") or {}

    def match_score(weights: dict[str, float]) -> float:
        total = 0.0
        for key, weight in weights.items():
            token = str(key).lower()
            if token in item_tokens:
                total += float(weight)
            elif any(token in value or value in token for value in item_tokens):
                total += 0.5 * float(weight)
        return total

    tags = tags_for(item)
    score = match_score(likes) - 1.5 * match_score(avoids)
    score -= 0.6 * abs(float(tags.get("niche", 0)) - float(profile.get("niche_level", 0)))
    if tags.get("logo_heavy") and any("logo" in rule.lower() for rule in profile.get("rules", [])):
        score -= 0.8
    return score


def eligible(
    item: dict[str, Any],
    brief: dict[str, Any],
    profile: dict[str, Any],
    *,
    exclude_ids: set[str] | None = None,
) -> bool:
    if item["id"] in (exclude_ids or set()):
        return False
    tags = tags_for(item)
    gender = brief.get("gender", "both")
    if gender == "menswear" and tags.get("gender") not in {"menswear", "unisex"}:
        return False
    if gender == "womenswear" and tags.get("gender") not in {"womenswear", "unisex"}:
        return False
    condition = profile.get("condition_preference", brief.get("condition", "both"))
    if condition != "both" and tags.get("condition") != condition:
        return False
    price = item.get("price_gbp")
    ceiling = float(profile.get("price_ceiling_gbp") or brief.get("budget_gbp") or 0)
    if price is None or float(price) > ceiling * 0.6:
        return False
    if brief.get("looking_for") == "pieces":
        requested = set(brief.get("pieces") or [])
        if requested and tags.get("slot") not in requested:
            return False
    return True


def rank_items(
    items: list[dict[str, Any]],
    brief: dict[str, Any],
    profile: dict[str, Any],
    *,
    exclude_ids: set[str] | None = None,
    core_profile: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    ranked = [
        (
            score_item(item, profile)
            + (0.3 * score_item(item, core_profile) if core_profile else 0.0),
            item,
        )
        for item in items
        if tags_for(item) and eligible(item, brief, profile, exclude_ids=exclude_ids)
    ]
    ranked.sort(
        key=lambda entry: (
            -entry[0],
            float(entry[1].get("price_gbp") or 0),
            entry[1].get("fetched_at") or "",
            entry[1]["id"],
        )
    )
    return [item for _, item in ranked]


def make_deck(
    ranked: list[dict[str, Any]],
    count: int,
    brief: dict[str, Any] | None = None,
    *,
    include_shoes: bool = True,
) -> list[dict[str, Any]]:
    brief = brief or {}
    shoe_cap = max(1, count // 8) if include_shoes and count else 0
    shoes = [item for item in ranked if tags_for(item).get("slot") == "shoes"]
    available_shoes = min(shoe_cap, len(shoes), count)
    remaining = [
        item for item in ranked if tags_for(item).get("slot") != "shoes"
    ]
    slots = list(dict.fromkeys(
        slot for slot in brief.get("pieces", []) if slot != "shoes"
    ))
    if not slots:
        slots = list(dict.fromkeys(
            str(tags_for(item).get("slot"))
            for item in remaining
            if tags_for(item).get("slot") not in (None, "shoes")
        ))
    deck: list[dict[str, Any]] = []
    seen_aesthetics: set[str] = set()
    next_slot = 0
    non_shoe_goal = max(0, count - available_shoes)
    while len(deck) < non_shoe_goal and remaining:
        candidates = [
            item for item in remaining
            if tags_for(item).get("slot") == slots[next_slot % len(slots)]
        ] if slots else list(remaining)
        next_slot += 1
        if not candidates:
            if slots and next_slot >= len(slots) * (len(deck) + len(slots) + 1):
                slots = [
                    slot for slot in slots
                    if any(tags_for(item).get("slot") == slot for item in remaining)
                ]
                next_slot = 0
                if not slots:
                    slots = []
            continue
        position = len(deck)
        chosen = candidates[0]
        if position % 3 == 2:
            chosen = next(
                (
                    item for item in candidates
                    if {
                        str(value).lower()
                        for value in tags_for(item).get("aesthetics", [])
                    } - seen_aesthetics
                ),
                chosen,
            )
        remaining.remove(chosen)
        deck.append(chosen)
        seen_aesthetics.update(
            str(value).lower() for value in tags_for(chosen).get("aesthetics", [])
        )
    if len(deck) < non_shoe_goal:
        for item in remaining:
            deck.append(item)
            if len(deck) >= non_shoe_goal:
                break
    deck.extend(shoes[: max(0, min(shoe_cap, count - len(deck)))])
    return deck[:count]
