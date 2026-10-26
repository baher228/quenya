import json
import random
import re
from typing import Any

from app.accessories import accessory_cap, is_accessory_slot
from app.gender_rules import text_gender

_KEY_PREFIX = re.compile(
    r"^(?:style|styles|aesthetic|aesthetics|color|colour|colors|colours|"
    r"category|categories|material|materials|fit|era|brand|brands|slot|"
    r"pattern|patterns|condition|gender)"
    r"(?=[:_\-\s])[:_\-\s]+"
)


def norm_token(value: Any) -> str:
    token = _KEY_PREFIX.sub("", str(value).strip().lower(), count=1)
    return " ".join(token.replace("_", " ").replace("-", " ").split())


def _whole_word_match(left: str, right: str) -> bool:
    left = norm_token(left)
    right = norm_token(right)
    if not left or not right:
        return False
    return bool(
        re.search(r"\b" + re.escape(left) + r"\b", right)
        or re.search(r"\b" + re.escape(right) + r"\b", left)
    )


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
    item_tokens = {norm_token(token) for token in tokens(item)}
    likes = profile.get("likes") or {}
    avoids = profile.get("avoids") or {}

    def match_score(weights: dict[str, float]) -> float:
        total = 0.0
        for key, weight in weights.items():
            token = norm_token(key)
            if not token:
                continue
            if token in item_tokens:
                total += float(weight)
            elif any(_whole_word_match(token, value) for value in item_tokens):
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
    availability = str(item.get("availability") or "").lower()
    if any(
        marker in availability
        for marker in (
            "outofstock", "out of stock", "out-of-stock", "soldout", "sold out",
            "sold_out", "unavailable", "closed",
        )
    ):
        return False
    tags = tags_for(item)
    gender = brief.get("gender", "both")
    if gender == "menswear" and tags.get("gender") not in {"menswear", "unisex"}:
        return False
    if gender == "womenswear" and tags.get("gender") not in {"womenswear", "unisex"}:
        return False
    text_gender_value = text_gender(
        item.get("title"), "", item.get("source_url")
    )
    if gender == "menswear" and text_gender_value == "womenswear":
        return False
    if gender == "womenswear" and text_gender_value == "menswear":
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


def secondhand_share(brief: dict[str, Any], profile: dict[str, Any]) -> float:
    condition = profile.get("condition_preference", brief.get("condition", "both"))
    if condition == "second-hand":
        return 1.0
    if condition == "new":
        return 0.0
    niche = float(profile.get("niche_level", brief.get("niche", 0.5)))
    per_piece = float(brief.get("budget_gbp") or 0) / (
        1 if brief.get("looking_for") == "pieces" else 4
    )
    share = 0.5 + 0.6 * (niche - 0.5)
    if per_piece and per_piece < 40:
        share += 0.15
    elif per_piece > 150:
        share -= 0.15
    return min(0.85, max(0.15, share))


def _is_secondhand(item: dict[str, Any]) -> bool:
    tags = tags_for(item)
    return tags.get("condition") == "second-hand" or (
        not tags.get("condition") and item.get("source") == "vinted"
    )


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
    secondhand_share: float = 0.5,
    selector=None,
) -> list[dict[str, Any]]:
    brief = brief or {}
    shoe_cap = max(1, count // 8) if include_shoes and count else 0
    accessory_limit = accessory_cap(count, brief)
    shoes = [item for item in ranked if tags_for(item).get("slot") == "shoes"]
    available_shoes = min(shoe_cap, len(shoes), count)
    remaining = [
        item for item in ranked
        if tags_for(item).get("slot") != "shoes"
        and (accessory_limit > 0 or not is_accessory_slot(tags_for(item).get("slot")))
    ]
    slots = (
        list(dict.fromkeys(
            slot for slot in brief.get("pieces", []) if slot != "shoes"
        ))
        if brief.get("looking_for") == "pieces"
        else []
    )
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
        secondhand_pool = [item for item in candidates if _is_secondhand(item)]
        new_pool = [item for item in candidates if not _is_secondhand(item)]
        target_secondhand = secondhand_share * (len(deck) + 1)
        preferred = (
            secondhand_pool
            if sum(_is_secondhand(item) for item in deck) < target_secondhand
            else new_pool
        )
        alternative = new_pool if preferred is secondhand_pool else secondhand_pool
        choice_pool = preferred or alternative
        choice_pool = choice_pool or candidates
        if position % 3 == 2:
            distinct = [
                item for item in choice_pool
                if {
                    str(value).lower() for value in tags_for(item).get("aesthetics", [])
                } - seen_aesthetics
            ]
            if distinct:
                choice_pool = distinct
        chosen = selector(choice_pool, deck) if selector else choice_pool[0]
        remaining.remove(chosen)
        deck.append(chosen)
        if (
            is_accessory_slot(tags_for(chosen).get("slot"))
            and sum(is_accessory_slot(tags_for(item).get("slot")) for item in deck)
            >= accessory_limit
        ):
            remaining = [
                item for item in remaining
                if not is_accessory_slot(tags_for(item).get("slot"))
            ]
            slots = [slot for slot in slots if not is_accessory_slot(slot)]
        seen_aesthetics.update(
            str(value).lower() for value in tags_for(chosen).get("aesthetics", [])
        )
    if len(deck) < non_shoe_goal:
        while remaining and len(deck) < non_shoe_goal:
            chosen = selector(remaining, deck) if selector else remaining[0]
            remaining.remove(chosen)
            deck.append(chosen)
    shoe_count = max(0, min(shoe_cap, count - len(deck)))
    if selector:
        remaining_shoes = list(shoes)
        for _ in range(shoe_count):
            if not remaining_shoes:
                break
            chosen = selector(remaining_shoes, deck)
            remaining_shoes.remove(chosen)
            deck.append(chosen)
    else:
        deck.extend(shoes[:shoe_count])
    return deck[:count]


def add_exploration(
    deck: list[dict[str, Any]],
    ranked: list[dict[str, Any]],
    count: int,
    profile: dict[str, Any],
    *,
    swipes: int,
    seed: str,
    brief: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    non_shoe_indices = [
        index
        for index, item in enumerate(deck)
        if tags_for(item).get("slot") != "shoes"
    ]
    rate = max(0.15, 0.35 - 0.005 * swipes)
    explore_count = min(round(rate * count), len(non_shoe_indices))
    deck_ids = {item["id"] for item in deck}
    accessory_limit = accessory_cap(count, brief)
    candidates = []
    candidate_ids = set(deck_ids)
    for item in ranked[2 * count :]:
        if item["id"] in candidate_ids or tags_for(item).get("slot") == "shoes":
            continue
        candidates.append(item)
        candidate_ids.add(item["id"])
    explore_count = min(explore_count, len(candidates))
    if explore_count == 0:
        return [{**item, "explore": False} for item in deck]

    pool = list(candidates)
    rng = random.Random(seed)
    explore_items = []
    removed = set()
    accessory_count = sum(
        is_accessory_slot(tags_for(item).get("slot")) for item in deck
    )
    removal_indexes = list(reversed(non_shoe_indices[-explore_count:]))
    for removed_index in removal_indexes:
        removed_item = deck[removed_index]
        remaining_accessories = accessory_count - int(
            is_accessory_slot(tags_for(removed_item).get("slot"))
        )
        choice_pool = [
            item for item in pool
            if not is_accessory_slot(tags_for(item).get("slot"))
            or remaining_accessories < accessory_limit
        ]
        if not choice_pool:
            break
        weights = [
            1.0 / (1.0 + max(0.0, -score_item(item, profile)))
            for item in choice_pool
        ]
        selected = rng.choices(range(len(choice_pool)), weights=weights, k=1)[0]
        chosen = choice_pool[selected]
        pool.remove(chosen)
        explore_items.append(chosen)
        removed.add(removed_index)
        accessory_count = remaining_accessories + int(
            is_accessory_slot(tags_for(chosen).get("slot"))
        )
    if not explore_items:
        return [{**item, "explore": False} for item in deck]
    remaining = [item for index, item in enumerate(deck) if index not in removed]
    explore_count = len(explore_items)

    length = len(remaining)
    shoe_boundary = next(
        (
            index
            for index, item in enumerate(remaining)
            if tags_for(item).get("slot") == "shoes"
        ),
        length,
    )
    result = [{**item, "explore": False} for item in remaining]
    for index, item in enumerate(explore_items):
        position = round((index + 1) * length / (explore_count + 1))
        position = min(position + index, shoe_boundary + index)
        result.insert(position, {**item, "explore": True})
    return result
