import json
import re
from urllib.parse import urlparse

WOMEN = re.compile(
    r"\b(women|womens|women's|woman|woman's|ladies|ladies'|lady's|wmns|"
    r"girls?|girl's|maternity|for her)\b",
    re.I,
)
MEN = re.compile(r"\b(men|mens|men's|for him|boys?|boy's)\b", re.I)
UNISEX = re.compile(r"\bunisex\b", re.I)
WOMEN_GARMENT = re.compile(
    r"\b(skirts?|bras?|bralettes?|bikinis?|dress(es)?)\b"
    r"(?![\s-]*(shirts?|shoes?|trousers?|pants?|boots?|watch|socks?|gowns?|coats?))",
    re.I,
)


def text_gender(title, product_type="", url=""):
    path = urlparse(url or "").path.replace("-", " ").replace("_", " ")
    text = " | ".join(x for x in (title or "", product_type or "", path) if x)
    if UNISEX.search(text):
        return "unisex"
    w, m = bool(WOMEN.search(text)), bool(MEN.search(text))
    if w and not m:
        return "womenswear"
    if m and not w:
        return "menswear"
    if not w and not m and WOMEN_GARMENT.search(title or ""):
        return "womenswear"
    return None


def product_type_for_item(item):
    raw = item.get("raw_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = {}
    if not isinstance(raw, dict):
        raw = {}
    record = raw.get("record")
    if not isinstance(record, dict):
        record = raw
    product_type = (
        record.get("product_type")
        or raw.get("product_type")
        or item.get("product_type")
        or ""
    )
    return str(product_type)
