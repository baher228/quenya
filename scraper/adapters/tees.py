import re

TEE = re.compile(r"\b(?:t|tee)[\s\-‐‑–]?shirts?\b|\btshirts?\b|\btees?\b", re.I)
NEGATIVE = re.compile(
    r"\b(?:sweat(?:shirt)?s?|hoodies?|hoody|hooded|polos?(?!\s+ralph)|shirts?|"
    r"overshirts?|shackets?|vests?|tanks?|tank\s?tops?|singlets?|sleeveless|"
    r"muscle\s+TEE|camis?|camisoles?|bodysuits?|bodies|bras?|bralettes?|dress(?:es)?|"
    r"skirts?|shorts|trousers?|pants|leggings|joggers|jackets?|coats?|gilets?|"
    r"jumpers?|sweaters?|cardigans?|knit(?:ted)?|fleeces?|quarter[\s-]?zip|"
    r"half[\s-]?zip|zip|pyjamas?|pajamas?|pj|nightshirts?|nightie|sleep\w*|"
    r"undershirts?|thermals?|base\s?layers?|rash\s?(?:guard|vest)s?|swim\w*|"
    r"wetsuits?|set|co-?ords?|bundle|vinyl|lp|cd|cassette|posters?|stickers?|"
    r"mugs?|totes?|bags?|caps?|hats?|beanies?|socks?|gift|vouchers?|golf\s+TEEs|"
    r"boxers?|briefs|trunks|underwear)\b",
    re.I,
)
REJECTED_PRODUCT_TYPE = re.compile(
    r"\b(?:kids?|youth|junior|baby|children|boy'?s|girl'?s|underwear|sleepwear|nightwear|"
    r"swim\w*|gift)\b",
    re.I,
)
TITLE_START_KIDS = re.compile(r"^\s*(?:boys?|girls?|kids?|youth)\b", re.I)
MULTIPACK = re.compile(
    r"\b\d+\s*-?\s*packs?\b|\bpack\s+of\s+\d+\b|\bmulti-?packs?\b",
    re.I,
)
NON_TEE_TITLE = re.compile(
    r"\b(?:tops?|henley\w*|jersey|turtle\s?neck|roll\s?neck|funnel\s?neck|"
    r"mock\s?neck|blouses?|bandeau|peplum|wrap|rugby|mesh|bodycon|corset|"
    r"bustier|crop)\b",
    re.I,
)
LONG_SLEEVE = re.compile(
    r"\blong[\s\-]?sleeved?s?\b|\blongsleeves?\b|\bl/s\b|\bls\b|\b3/4\s+sleeve",
    re.I,
)
TAG_CATEGORIES = {
    "t-shirt",
    "t-shirts",
    "tee",
    "tees",
    "tshirt",
    "tshirts",
    "short sleeve t-shirts",
    "long sleeve t-shirts",
}
REJECTED_TAGS = {"kids", "kid", "kidswear", "youth", "junior", "children", "boys", "girls"}
WOMEN_TAG = re.compile(r"\b(?:women|womens|woman|female|ladies|womenswear)\b", re.I)
MEN_TAG = re.compile(r"\b(?:men|mens|man|male|menswear)\b", re.I)
UNISEX_TAG = re.compile(r"\bunisex\b", re.I)


def tee_category(title, product_type="", tags=()) -> str | None:
    title = str(title or "")
    product_type = str(product_type or "")
    if isinstance(tags, str):
        tags = tags.split(",")
    tags = [str(tag).strip().lower() for tag in tags]
    if (
        REJECTED_PRODUCT_TYPE.search(product_type)
        or MULTIPACK.search(title)
        or MULTIPACK.search(product_type)
        or TITLE_START_KIDS.search(title)
    ):
        return None
    tag_tokens = {
        token
        for tag in tags
        for token in re.split(r"[\s:_|/\-]+", tag)
        if token
    }
    if tag_tokens.intersection(REJECTED_TAGS):
        return None

    title_without_tee = TEE.sub(" TEE ", title)
    if NEGATIVE.search(title_without_tee):
        return None

    tag_categories = {
        re.sub(r"^category:", "", tag, flags=re.I).strip()
        for tag in tags
    }
    title_has_tee = bool(TEE.search(title))
    has_tee_evidence = (
        title_has_tee
        or TEE.search(product_type)
        or tag_categories.intersection(TAG_CATEGORIES)
    )
    if not has_tee_evidence:
        return None
    if not title_has_tee:
        if NEGATIVE.search(TEE.sub(" TEE ", product_type)) or NON_TEE_TITLE.search(title):
            return None
    return "long sleeve t-shirt" if LONG_SLEEVE.search(title) else "t-shirt"


def tee_gender(shop, product, url, collection_gender=None) -> str | None:
    from app.gender_rules import text_gender

    from .shopify import gender_for

    title = str(product.get("title") or "")
    product_type = str(product.get("product_type") or "")
    text_gender_value = text_gender(title, product_type, url)
    if text_gender_value == "womenswear":
        return "w"
    if text_gender_value == "menswear":
        return "m"
    if text_gender_value == "unisex":
        return None

    gender_rule = shop.get("gender_rule")
    if gender_rule in {"belstaff", "sunspel"}:
        return gender_for(product, gender_rule)

    tags = product.get("tags") or []
    if isinstance(tags, str):
        tags = tags.split(",")
    women = men = unisex = False
    for tag in tags:
        value = re.sub(r"[:_\-/]", " ", str(tag))
        women |= bool(WOMEN_TAG.search(value))
        men |= bool(MEN_TAG.search(value))
        unisex |= bool(UNISEX_TAG.search(value))
    if women and not men:
        return "w"
    if men and not women:
        return "m"
    if collection_gender in {"m", "w"}:
        return collection_gender
    if unisex or (women and men):
        return None
    if gender_rule in {"men", "women", "selfportrait"}:
        return {"men": "m", "women": "w", "selfportrait": "w"}[gender_rule]
    default = shop.get("tee_default_gender")
    return default if default in {"m", "w"} else None
