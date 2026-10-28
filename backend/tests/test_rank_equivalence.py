import json
import random
import re

from app import rank
from app.gender_rules import text_gender

_REFERENCE_KEY_PREFIX = re.compile(
    r"^(?:style|styles|aesthetic|aesthetics|color|colour|colors|colours|"
    r"category|categories|material|materials|fit|era|brand|brands|slot|"
    r"pattern|patterns|condition|gender)"
    r"(?=[:_\-\s])[:_\-\s]+"
)


def _reference_norm_token(value):
    token = _REFERENCE_KEY_PREFIX.sub("", str(value).strip().lower(), count=1)
    return " ".join(token.replace("_", " ").replace("-", " ").split())


def _reference_whole_word_match(left, right):
    left = _reference_norm_token(left)
    right = _reference_norm_token(right)
    if not left or not right:
        return False
    return bool(
        re.search(r"\b" + re.escape(left) + r"\b", right)
        or re.search(r"\b" + re.escape(right) + r"\b", left)
    )


def _reference_tags_for(item):
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


def _reference_tokens(item):
    tags = _reference_tags_for(item)
    values = []
    for key in ("aesthetics", "colours", "materials"):
        values.extend(tags.get(key) or [])
    values.extend((tags.get("category"), tags.get("fit"), tags.get("era")))
    return {
        str(value).strip().lower()
        for value in values
        if value and str(value).strip().lower() != "unknown"
    }


def _reference_score_item(item, profile):
    item_tokens = {_reference_norm_token(token) for token in _reference_tokens(item)}
    likes = profile.get("likes") or {}
    avoids = profile.get("avoids") or {}

    def match_score(weights):
        total = 0.0
        for key, weight in weights.items():
            token = _reference_norm_token(key)
            if not token:
                continue
            if token in item_tokens:
                total += float(weight)
            elif any(_reference_whole_word_match(token, value) for value in item_tokens):
                total += 0.5 * float(weight)
        return total

    tags = _reference_tags_for(item)
    score = match_score(likes) - 1.5 * match_score(avoids)
    score -= 0.6 * abs(float(tags.get("niche", 0)) - float(profile.get("niche_level", 0)))
    if tags.get("logo_heavy") and any("logo" in rule.lower() for rule in profile.get("rules", [])):
        score -= 0.8
    return score


def _reference_eligible(item, brief, profile, *, exclude_ids=None):
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
    tags = _reference_tags_for(item)
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


def _reference_rank_items(
    items,
    brief,
    profile,
    *,
    exclude_ids=None,
    core_profile=None,
):
    ranked = [
        (
            _reference_score_item(item, profile)
            + (0.3 * _reference_score_item(item, core_profile) if core_profile else 0.0),
            item,
        )
        for item in items
        if _reference_tags_for(item)
        and _reference_eligible(item, brief, profile, exclude_ids=exclude_ids)
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


def _synthetic_items(count):
    rng = random.Random(74021)
    styles = [
        "quiet luxury", "gorpcore", "dark academia", "workwear",
        "streetwear", "minimalism", "city utility", "heritage",
    ]
    colours = ["off white", "navy", "burgundy", "olive", "black", "rust"]
    materials = ["wool", "cotton", "leather", "nylon", "linen", "polyester"]
    categories = ["knitwear", "outerwear", "shoes", "dress", "accessory", "top"]
    genders = ["menswear", "womenswear", "unisex"]
    conditions = ["new", "second-hand"]
    slots = ["knitwear", "outerwear", "shoes", "dress", "accessory", "top"]
    items = []
    for index in range(count):
        gender = rng.choice(genders)
        gender_word = (
            "Men's" if gender == "menswear"
            else "Women's" if gender == "womenswear"
            else "Unisex"
        )
        style = rng.choice(styles)
        material = rng.choice(materials)
        category = rng.choice(categories)
        tags = {
            "aesthetics": [style, rng.choice(styles)],
            "colours": [rng.choice(colours), rng.choice(colours)],
            "materials": [material, rng.choice(materials)],
            "category": category,
            "fit": rng.choice(["relaxed", "tailored", "oversized", "slim"]),
            "era": rng.choice(["90s", "vintage", "modern", "70s"]),
            "gender": gender,
            "condition": rng.choice(conditions),
            "slot": rng.choice(slots),
            "niche": round(rng.random(), 2),
            "logo_heavy": index % 7 == 0,
        }
        if index % 101 == 0:
            tags["aesthetics"][0] = "style:color:blue"
        items.append(
            {
                "id": f"synthetic-{index:04d}",
                "source": "vinted" if index % 2 else "shop",
                "source_url": f"https://shop{index % 11}.example/items/{index:04d}",
                "title": f"{gender_word} {style} {material} {category} {index:04d}",
                "price_gbp": float(10 + index % 280),
                "fetched_at": f"2026-01-{1 + index % 28:02d}T00:00:00Z",
                "availability": "sold out" if index % 97 == 0 else "available",
                "tags": tags,
            }
        )
    return items


def test_rank_items_matches_reference_scores_and_order():
    items = _synthetic_items(2400)
    profiles = [
        {
            "likes": {
                "style:quiet_luxury": 0.9,
                "aesthetic:dark-academia": 0.7,
                "colour_off_white": 0.6,
                "materials:wool": 0.55,
                "category:outerwear": 0.5,
                "fit relaxed": 0.3,
                "style:city-utility": 0.45,
                "color": 0.4,
            },
            "avoids": {
                "material:polyester": 0.8,
                "color_red": 0.3,
                "category:accessory": 0.2,
                "fit:slim": 0.35,
            },
            "niche_level": 0.4,
            "condition_preference": "both",
            "price_ceiling_gbp": 300,
            "rules": ["avoid logo-heavy items"],
        },
        {
            "likes": {
                "styles_gorpcore": 0.8,
                "colour:burgundy": 0.6,
                "material:leather": 0.5,
                "era:90s": 0.4,
                "category_dress": 0.25,
            },
            "avoids": {
                "style:streetwear": 0.7,
                "colour:off white": 0.3,
                "fit_oversized": 0.4,
            },
            "niche_level": 0.7,
            "condition_preference": "second-hand",
            "price_ceiling_gbp": 210,
            "rules": [],
        },
        {
            "likes": {
                "style:heritage": 0.75,
                "aesthetics:quiet luxury": 0.65,
                "materials_cotton": 0.45,
                "category:knitwear": 0.6,
            },
            "avoids": {
                "material:nylon": 0.5,
                "era-modern": 0.35,
                "fit:tailored": 0.2,
            },
            "niche_level": 0.2,
            "condition_preference": "new",
            "price_ceiling_gbp": 250,
            "rules": ["logo"],
        },
    ]
    briefs = [
        {"gender": "both", "condition": "both", "budget_gbp": 300, "looking_for": "outfits"},
        {
            "gender": "menswear",
            "condition": "second-hand",
            "budget_gbp": 210,
            "looking_for": "pieces",
            "pieces": ["outerwear", "knitwear"],
        },
        {"gender": "womenswear", "condition": "new", "budget_gbp": 250, "looking_for": "outfits"},
    ]
    core_profiles = [None, None, profiles[0]]
    exclude_ids = {f"synthetic-{index:04d}" for index in range(0, len(items), 173)}

    for brief, profile, core_profile in zip(briefs, profiles, core_profiles, strict=True):
        for item in items:
            assert rank.score_item(item, profile) == _reference_score_item(item, profile)
            if core_profile:
                assert rank.score_item(item, core_profile) == _reference_score_item(
                    item,
                    core_profile,
                )
        assert rank.rank_items(
            items,
            brief,
            profile,
            exclude_ids=exclude_ids,
            core_profile=core_profile,
        ) == _reference_rank_items(
            items,
            brief,
            profile,
            exclude_ids=exclude_ids,
            core_profile=core_profile,
        )
