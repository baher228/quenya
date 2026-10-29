import json
import random
import re
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import numpy as np

from app.accessories import accessory_cap, is_accessory_slot
from app.gender_rules import text_gender
from app.images import normalize_image_url

DISLIKE_LOOKALIKE_COS = 0.90
LIKE_LOOKALIKE_COS = 0.95

_KEY_PREFIX = re.compile(
    r"^(?:style|styles|aesthetic|aesthetics|color|colour|colors|colours|"
    r"category|categories|material|materials|fit|era|brand|brands|slot|"
    r"pattern|patterns|condition|gender)"
    r"(?=[:_\-\s])[:_\-\s]+"
)


def product_key(item: dict[str, Any]) -> str:
    source_url = str(item.get("source_url") or "")
    try:
        shop = urlsplit(source_url).netloc.lower()
    except ValueError:
        shop = ""
    if not shop:
        shop = str(item.get("source") or "").lower()
    shop = shop.removeprefix("www.")

    title = str(item.get("title") or "").strip().lower()
    before_variant, separator, _variant = title.rpartition(" - ")
    if separator and len(before_variant.split()) >= 2:
        title = before_variant
    title = re.sub(r"[^\w\s]|_", " ", title)
    return f"{shop}|{' '.join(title.split())}"


def image_key(item: dict[str, Any]) -> str | None:
    image_url = item.get("image_url")
    if not isinstance(image_url, str) or not image_url.strip():
        return None
    image_url = image_url.strip()
    try:
        image_url = normalize_image_url(image_url)
        parts = urlsplit(image_url)
    except ValueError:
        return image_url.split("?", maxsplit=1)[0].split("#", maxsplit=1)[0]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _unit_vector(value: Any) -> np.ndarray | None:
    try:
        vector = np.asarray(value, dtype=np.float32).reshape(-1)
    except (TypeError, ValueError):
        return None
    if not vector.size or not np.isfinite(vector).all():
        return None
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 0 else None


def drop_lookalikes(
    ranked: list[dict[str, Any]],
    swiped_items: list[tuple[dict[str, Any], bool]],
    vectors: dict[str, Any],
    *,
    dislike_cos: float = DISLIKE_LOOKALIKE_COS,
    like_cos: float = LIKE_LOOKALIKE_COS,
    vector_window: int = 800,
) -> list[dict[str, Any]]:
    blocked_products = {product_key(item) for item, _liked in swiped_items}
    blocked_images = {
        key
        for item, _liked in swiped_items
        if (key := image_key(item)) is not None
    }
    survivors = []
    seen_products = set()
    seen_images = set()
    for item in ranked:
        product = product_key(item)
        image = image_key(item)
        if product in blocked_products or (image and image in blocked_images):
            continue
        if product in seen_products or (image and image in seen_images):
            continue
        seen_products.add(product)
        if image:
            seen_images.add(image)
        survivors.append(item)

    swiped_vectors: dict[tuple[bool, int], list[np.ndarray]] = {}
    for item, liked in swiped_items:
        vector = _unit_vector(vectors.get(item.get("id")))
        if vector is not None:
            swiped_vectors.setdefault((liked, vector.size), []).append(vector)

    candidate_vectors: dict[int, list[tuple[int, np.ndarray]]] = {}
    vector_count = 0
    for index, item in enumerate(survivors):
        if vector_count >= max(0, vector_window):
            break
        vector = _unit_vector(vectors.get(item.get("id")))
        if vector is None:
            continue
        vector_count += 1
        candidate_vectors.setdefault(vector.size, []).append((index, vector))

    removed = set()
    for (liked, size), swiped_group in swiped_vectors.items():
        candidates = candidate_vectors.get(size)
        if not candidates:
            continue
        candidate_matrix = np.stack([vector for _, vector in candidates])
        swiped_matrix = np.stack(swiped_group)
        similarities = candidate_matrix @ swiped_matrix.T
        thresholds = like_cos if liked else dislike_cos
        removed.update(
            candidates[index][0]
            for index in np.flatnonzero(similarities.max(axis=1) >= thresholds)
        )
    return [item for index, item in enumerate(survivors) if index not in removed]


@lru_cache(maxsize=65536)
def _norm_token(value: str) -> str:
    token = _KEY_PREFIX.sub("", value.strip().lower(), count=1)
    return " ".join(token.replace("_", " ").replace("-", " ").split())


def norm_token(value: Any) -> str:
    return _norm_token(str(value))


@lru_cache(maxsize=4096)
def _whole_word_pattern(value: str) -> re.Pattern:
    return re.compile(r"\b" + re.escape(value) + r"\b")


@lru_cache(maxsize=16384)
def _whole_word_match_normalized(left: str, right: str) -> bool:
    if not left or not right:
        return False
    return bool(_whole_word_pattern(left).search(right) or _whole_word_pattern(right).search(left))


def _whole_word_match(left: str, right: str) -> bool:
    left = norm_token(left)
    right = norm_token(right)
    if right < left:
        left, right = right, left
    return _whole_word_match_normalized(left, right)


def _match_normalized_words(left: str, right: str) -> bool:
    if right < left:
        left, right = right, left
    return _whole_word_match_normalized(left, right)


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


def _tokens_from_tags(tags: dict[str, Any]) -> set[str]:
    values: list[Any] = []
    for key in ("aesthetics", "colours", "materials"):
        values.extend(tags.get(key) or [])
    values.extend((tags.get("category"), tags.get("fit"), tags.get("era")))
    return {
        str(value).strip().lower()
        for value in values
        if value and str(value).strip().lower() != "unknown"
    }


def tokens(item: dict[str, Any]) -> set[str]:
    return _tokens_from_tags(tags_for(item))


def _normalized_weights(
    weights: dict[str, float],
) -> tuple[tuple[str, str, Any], ...]:
    normalized = []
    for key, weight in weights.items():
        token = norm_token(key)
        normalized.append((token, norm_token(token), weight))
    return tuple(normalized)


def _profile_weights(
    profile: dict[str, Any],
) -> tuple[
    tuple[tuple[str, str, Any], ...],
    tuple[tuple[str, str, Any], ...],
]:
    return (
        _normalized_weights(profile.get("likes") or {}),
        _normalized_weights(profile.get("avoids") or {}),
    )


@lru_cache(maxsize=32768)
def _match_score(
    item_tokens: tuple[frozenset[str], frozenset[str]],
    weights: tuple[tuple[str, str, Any], ...],
) -> float:
    normalized_tokens, whole_word_tokens = item_tokens
    total = 0.0
    for token, whole_word_token, weight in weights:
        if not token:
            continue
        if token in normalized_tokens:
            total += float(weight)
        elif any(
            _match_normalized_words(whole_word_token, value)
            for value in whole_word_tokens
        ):
            total += 0.5 * float(weight)
    return total


def _normalized_item_tokens(
    tags: dict[str, Any],
) -> tuple[frozenset[str], frozenset[str]]:
    normalized_tokens = frozenset(
        norm_token(token) for token in _tokens_from_tags(tags)
    )
    whole_word_tokens = frozenset(
        norm_token(token) for token in normalized_tokens
    )
    return normalized_tokens, whole_word_tokens


def _score_item(
    item: dict[str, Any],
    profile: dict[str, Any],
    tags: dict[str, Any],
    item_tokens: tuple[frozenset[str], frozenset[str]],
    weights: tuple[
        tuple[tuple[str, str, Any], ...],
        tuple[tuple[str, str, Any], ...],
    ],
) -> float:
    likes, avoids = weights
    score = _match_score(item_tokens, likes) - 1.5 * _match_score(item_tokens, avoids)

    score -= 0.6 * abs(float(tags.get("niche", 0)) - float(profile.get("niche_level", 0)))
    if tags.get("logo_heavy") and any("logo" in rule.lower() for rule in profile.get("rules", [])):
        score -= 0.8
    return score


def score_item(item: dict[str, Any], profile: dict[str, Any]) -> float:
    tags = tags_for(item)
    return _score_item(
        item,
        profile,
        tags,
        _normalized_item_tokens(tags),
        _profile_weights(profile),
    )


def eligible(
    item: dict[str, Any],
    brief: dict[str, Any],
    profile: dict[str, Any],
    *,
    exclude_ids: set[str] | None = None,
) -> bool:
    if exclude_ids is not None and item["id"] in exclude_ids:
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
    if gender in {"menswear", "womenswear"}:
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
    weights = _profile_weights(profile)
    core_weights = _profile_weights(core_profile) if core_profile else None
    ranked = []
    for item in items:
        tags = tags_for(item)
        if not tags or not eligible(item, brief, profile, exclude_ids=exclude_ids):
            continue
        item_tokens = _normalized_item_tokens(tags)
        score = _score_item(item, profile, tags, item_tokens, weights)
        if core_profile and core_weights:
            score += 0.3 * _score_item(item, core_profile, tags, item_tokens, core_weights)
        ranked.append((score, item))
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
    pieces = brief.get("pieces") or []
    pieces_mode = brief.get("looking_for") == "pieces" and bool(pieces)
    if pieces_mode:
        rotation_key_by_id = {
            item["id"]: tags_for(item).get("slot") for item in remaining
        }
        slots = list(dict.fromkeys(slot for slot in pieces if slot != "shoes"))
        if not slots:
            slots = list(dict.fromkeys(
                str(rotation_key_by_id[item["id"]])
                for item in remaining
                if rotation_key_by_id[item["id"]] not in (None, "shoes")
            ))
    else:
        from app.outfits import layer_role

        def _deck_kind(item: dict[str, Any]) -> str:
            role = layer_role(item)
            return role or str(tags_for(item).get("slot"))

        rotation_key_by_id = {
            item["id"]: _deck_kind(item) for item in remaining
        }
        slots = list(dict.fromkeys(
            rotation_key_by_id[item["id"]]
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
            if rotation_key_by_id[item["id"]] == slots[next_slot % len(slots)]
        ] if slots else list(remaining)
        next_slot += 1
        if not candidates:
            if slots and next_slot >= len(slots) * (len(deck) + len(slots) + 1):
                slots = [
                    slot for slot in slots
                    if any(
                        rotation_key_by_id[item["id"]] == slot
                        for item in remaining
                    )
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
