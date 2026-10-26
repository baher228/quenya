import re

from app.gender_rules import text_gender

_ACCESSORY_PATTERNS = (
    ("bag", "bag", r"\b(?:belt|bum)\s+bag\b|\bfanny\s+pack\b"),
    ("bag", "tote bag", r"\btotes?(?:\s+bags?)?\b"),
    ("bag", "backpack", r"\b(?:backpacks?|rucksacks?)\b"),
    ("bag", "holdall", r"\bholdalls?\b"),
    ("bag", "duffel bag", r"\bduffels?(?:\s+bags?)?\b"),
    ("bag", "weekender bag", r"\bweekenders?\s+bags?\b"),
    ("bag", "crossbody bag", r"\bcross[\s-]?body(?:\s+bags?)?\b"),
    ("bag", "shoulder bag", r"\bshoulder\s+bags?\b"),
    ("bag", "handbag", r"\bhandbags?\b"),
    ("bag", "clutch", r"\bclutches\b|\bclutch\b"),
    ("bag", "satchel", r"\bsatchels?\b"),
    ("bag", "messenger bag", r"\bmessenger(?:\s+bags?)?\b"),
    ("bag", "briefcase", r"\bbriefcases?\b"),
    ("bag", "bag", r"\bbags?\b"),
    ("accessory", "belt", r"\bbelts?\b"),
    ("accessory", "bucket hat", r"\bbucket\s+hats?\b"),
    ("accessory", "baseball cap", r"\bbaseball\s+caps?\b"),
    ("accessory", "flat cap", r"\bflat\s+caps?\b"),
    ("accessory", "beanie", r"\bbeanies?\b"),
    ("accessory", "beret", r"\bberets?\b"),
    ("accessory", "fedora", r"\bfedoras?\b"),
    ("accessory", "cap", r"\bcaps?\b"),
    ("accessory", "hat", r"\bhats?\b"),
    ("accessory", "scarf", r"\bscarves\b|\bscarfs?\b"),
    ("accessory", "snood", r"\bsnoods?\b"),
    ("accessory", "bandana", r"\bbandanas?\b"),
    ("accessory", "neckerchief", r"\bneckerchiefs?\b"),
    ("accessory", "shawl", r"\bshawls?\b"),
    ("accessory", "sunglasses", r"\bsunglasses\b|\bsunnies\b"),
    ("accessory", "necklace", r"\bnecklaces?\b"),
    ("accessory", "pendant", r"\bpendants?\b"),
    ("accessory", "bracelet", r"\bbracelets?\b"),
    ("accessory", "bangle", r"\bbangles?\b"),
    ("accessory", "ear cuff", r"\bear[\s-]?cuffs?\b"),
    ("accessory", "earrings", r"\b(?:earrings?|ear\s+rings?)\b"),
    ("accessory", "ring", r"\brings?\b|\bsignet\b"),
    ("accessory", "cufflinks", r"\bcufflinks?\b"),
    ("accessory", "brooch", r"\bbrooches\b|\bbrooch\b"),
    ("accessory", "anklet", r"\banklets?\b"),
    ("accessory", "chain", r"\bchains?\b"),
    ("accessory", "jewellery", r"\bjewellery\b|\bjewelry\b"),
    ("accessory", "watch", r"\bwatches\b|\bwatch\b"),
    ("accessory", "wallet", r"\bwallets?\b"),
    ("accessory", "card holder", r"\bcard[\s-]?holders?\b"),
    ("accessory", "card case", r"\bcard\s+cases?\b"),
    ("accessory", "coin purse", r"\bcoin\s+purses?\b"),
    ("accessory", "purse", r"\bpurses?\b"),
    ("accessory", "billfold", r"\bbillfolds?\b"),
    ("accessory", "money clip", r"\bmoney\s+clips?\b"),
    ("accessory", "gloves", r"\bgloves?\b"),
    ("accessory", "mittens", r"\bmittens?\b"),
    ("accessory", "socks", r"\bsocks?\b"),
)

_GARMENT_PATTERN = re.compile(
    r"\b(?:dress(?:es)?|jackets?|coats?|trousers|jeans|skirts?|shorts?|shirts?|"
    r"t[\s-]?shirts?|tees?|tops?|blouses?|jumpers?|sweaters?|cardigans?|"
    r"hoodies?|sweatshirts?|knitwear|knits?|blazers?|gilets?|vests?|"
    r"shoes?|trainers?|sneakers?|boots?|loafers?|sandals?|slippers?|cap\s+sleeves?)\b",
    re.IGNORECASE,
)
_COMPILED_ACCESSORY_PATTERNS = tuple(
    (slot, category, re.compile(pattern, re.IGNORECASE))
    for slot, category, pattern in _ACCESSORY_PATTERNS
)

_EXCLUDED_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bbeauty\b|\bcosmetics?\b|\bmake[\s-]?up\b|\bskincare\b|\bhaircare\b",
        r"\blipstick\b|\blip\s+balm\b|\bmascara\b|\bnail\s+polish\b|\bserums?\b",
        r"\bmoisturisers?\b|\bmoisturizers?\b|\bshampoos?\b|\bconditioners?\b",
        r"\bbody\s+wash\b|\bshower\s+gel\b|\bhand\s+cream\b|\bdeodorants?\b",
        r"\bsoaps?\b|\btoiletries\b|\bwash\s*bags?\b",
        r"\bfragrance\b|\bperfumes?\b|\bparfum\b|\beau\s+de\s+toilette\b",
        r"\beau\s+de\s+parfum\b|\bcolognes?\b|\bcandles?\b|\bdiffusers?\b|\bincense\b",
        r"\bhomeware\b|\bhome\b|\bcushions?\b|\bblankets?\b|\bthrows?\b|\btowels?\b",
        r"\bmugs?\b|\bcups?\b|\bglasses?\b|\bplates?\b|\bbowls?\b|\bvases?\b",
        r"\brugs?\b|\bposters?\b|\bframes?\b|\bornaments?\b|\blaundry\b",
        r"\bdust\s+bags?\b|\bgift\s+bags?\b|\bshoe\s+bags?\b|\bsleeping\s+bags?\b",
        r"\bbean\s+bags?\b|\bbooks?\b|\bmagazines?\b|\bzines?\b|\bjournals?\b",
        r"\bnotebooks?\b|\bdiaries\b|\bstationery\b|\bpens?\b",
        r"\bgreeting\s+cards?\b|\bphone\s+cases?\b|\biphone\b|\bairpods?\b",
        r"\bheadphones\b|\bearbuds\b|\bchargers?\b|\bcables\b|\bpower\s+banks?\b",
        r"\bspeakers?\b|\blaptops?\b|\btech\b|\bgift\s+cards?\b|\be[\s-]?gift\b",
        r"\bvouchers?\b|\bgift\s+wrap\b|\bgift\s+boxes?\b|\bgift\s+sets?\b",
        r"\bkids?\b|\bkid['’]s\b|\bchildren\b|\bbabies\b|\binfants?\b|\btoddlers?\b",
        r"\bjuniors?\b|\byouth\b|\bboys?\b|\bgirls?\b|\bage\s*\d{1,2}\b|\d{1,2}\s*yrs?\b",
        r"\bpets?\b|\bdogs?\b|\bcats?\b|\bleads?\b|\bcollars?\b",
        r"\bkeyrings?\b|\bkey\s+rings?\b|\bkeychains?\b",
        r"\bsamples?\b|\bbundle\s+fees?\b|\binsurance\b|\brepairs?\b|\bservices?\b",
        r"\bshipping\b|\bmemberships?\b|\bsubscriptions?\b|\bdonations?\b",
        r"\bglasses\s+cases?\b|\bsunglasses\s+cases?\b|\bcleaning\s+cloths?\b|\blenses\b",
        r"\bjewellery\s+boxes?\b|\bjewelry\s+boxes?\b|\bjewellery\s+dishes\b",
        r"\bwatch\s+boxes?\b|\bwatch\s+winders?\b|\bwatch\s+straps?\b",
    )
)


def is_excluded(title: str, product_type: str = "") -> bool:
    title = str(title or "")
    product_type = str(product_type or "")
    if any(pattern.search(f"{title} {product_type}") for pattern in _EXCLUDED_PATTERNS):
        return True
    return bool(re.search(r"\bprint\b", product_type, re.IGNORECASE))


def _accessory_match(value: str) -> tuple[str, str, re.Match] | None:
    matches = [
        (slot, category, match)
        for slot, category, pattern in _COMPILED_ACCESSORY_PATTERNS
        for match in pattern.finditer(value)
    ]
    if not matches:
        return None
    match = max(matches, key=lambda item: (item[2].end(), item[2].end() - item[2].start()))
    last_garment = None
    for garment in _GARMENT_PATTERN.finditer(value):
        last_garment = garment
    if last_garment and match[2].end() <= last_garment.end():
        return None
    return match


def accessory_kind(
    title: str, product_type: str = "", tags: tuple | list = ()
) -> tuple[str, str] | None:
    title = str(title or "")
    product_type = str(product_type or "")
    if is_excluded(title, product_type):
        return None
    match = _accessory_match(title)
    if match:
        return match[0], match[1]
    if _GARMENT_PATTERN.search(title):
        return None
    match = _accessory_match(product_type)
    if match:
        return match[0], match[1]
    if isinstance(tags, str):
        tags = (tag.strip() for tag in tags.split(","))
    for tag in tags or ():
        match = _accessory_match(str(tag))
        if match:
            return match[0], match[1]
    return None


def accessory_gender(
    title: str, product_type: str = "", url: str = "", category: str = ""
) -> str:
    gender = text_gender(title, product_type, url)
    if gender:
        return gender
    if category in {"handbag", "clutch"}:
        return "womenswear"
    if category in {"cufflink", "cufflinks"}:
        return "menswear"
    return "unisex"


def wants_accessories(brief: dict) -> bool:
    if set(brief.get("pieces") or ()) & {"bag", "accessory"}:
        return True
    text = str(brief.get("text") or "")
    if re.search(r"\baccessor(?:y|ies)\b", text, re.IGNORECASE):
        return True
    return _accessory_match(text) is not None


def accessory_cap(count: int, brief: dict | None) -> int:
    return count if wants_accessories(brief or {}) else count // 10


def is_accessory_slot(slot: str) -> bool:
    return slot in {"bag", "accessory"}
