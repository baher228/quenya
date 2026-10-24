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
) -> list[dict[str, Any]]:
    ranked = [
        (score_item(item, profile), item)
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


def make_deck(ranked: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    remaining = list(ranked)
    deck: list[dict[str, Any]] = []
    seen_slots: set[str] = set()
    seen_aesthetics: set[str] = set()
    for position in range(count):
        explore = position % 3 == 2
        chosen_index = 0
        if explore:
            for index, item in enumerate(remaining):
                tags = tags_for(item)
                aesthetics = {str(value).lower() for value in tags.get("aesthetics", [])}
                if tags.get("slot") not in seen_slots or aesthetics - seen_aesthetics:
                    chosen_index = index
                    break
        item = remaining.pop(chosen_index)
        tags = tags_for(item)
        seen_slots.add(str(tags.get("slot", "")).lower())
        seen_aesthetics.update(str(value).lower() for value in tags.get("aesthetics", []))
        deck.append(item)
        if not remaining:
            break
    return deck
