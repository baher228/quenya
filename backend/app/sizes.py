import json
import re
from functools import lru_cache
from typing import Any

from app.rank import tags_for

_LABEL_SIZE = re.compile(r"\bSize:\s*(.*?)(?:,\s|$)", re.IGNORECASE)
_TITLE_SIZE = re.compile(
    r"\b(?:size|sz)\s*[:#]?\s*(?P<size>"
    r"(?:UK\s*)?\d{1,2}(?:\.\d)?(?:\s*[-–/x]\s*\d{1,2}(?:\.\d)?)?"
    r"|W\s*\d{1,2}(?:\s*L\s*\d{1,2})?"
    r"|(?:X[\s-]?SMALL|X[\s-]?LARGE|SMALL|MEDIUM|LARGE|XXXL|XXL|XXS|"
    r"2XL|3XL|XL|XS|S|M|L))\b",
    re.IGNORECASE,
)
_TITLE_WAIST = re.compile(
    r"\b(?:W\s*\d{2}(?:\s*L\s*\d{2})?|\d{2}\s*[x/]\s*\d{2}|\d{2}\s*W)\b",
    re.IGNORECASE,
)
_PAREN_SIZE = re.compile(
    r"\(\s*(X[\s-]?SMALL|X[\s-]?LARGE|SMALL|MEDIUM|LARGE|XXXL|XXL|XXS|"
    r"2XL|3XL|XL|XS|S|M|L)\s*\)",
    re.IGNORECASE,
)
_TOP_LABEL = re.compile(
    r"(?<![A-Z])(?P<size>X[\s-]?SMALL|X[\s-]?LARGE|SMALL|MEDIUM|LARGE|"
    r"XXXL|XXL|XXS|2XL|3XL|XL|XS|S|M|L)(?![A-Z])",
    re.IGNORECASE,
)
_UK_TOP_RANGE = re.compile(
    r"\b(?:UK\s*)?(\d{1,2})\s*[-–]\s*(\d{1,2})\b",
    re.IGNORECASE,
)
_TOP_NUMBER = re.compile(r"\b(?:UK\s*)?(\d{1,2})\b", re.IGNORECASE)
_ONE_SIZE = re.compile(r"\b(?:one[\s-]?size|os)\b", re.IGNORECASE)
_WAIST_W_PREFIX = re.compile(r"\bW\s*(\d{2})\b", re.IGNORECASE)
_WAIST_W_SUFFIX = re.compile(r"\b(\d{2})\s*W\b", re.IGNORECASE)
_WAIST_PAIR = re.compile(r"\b(\d{2})\s*[/x]\s*\d{2}\b", re.IGNORECASE)
_WAIST_NAMED = re.compile(r"\bwaist\s*(\d{2})\b", re.IGNORECASE)
_WAIST_NUMBER = re.compile(r"(?<![\d./x])(\d{2})(?![\d./x])", re.IGNORECASE)
_UK_SHOE_SIZE = re.compile(r"\bUK\s*(\d{1,2}(?:\.\d+)?)\b", re.IGNORECASE)
_EU_SHOE_SIZE = re.compile(r"\bEU\s*(\d{2}(?:\.\d+)?)\b", re.IGNORECASE)
_SHOE_NUMBER = re.compile(r"\b(\d{1,2}(?:\.\d+)?)\b")

_TOP_SIZE_INDEX = {
    "XXS": 0,
    "XS": 1,
    "XSMALL": 1,
    "S": 2,
    "SMALL": 2,
    "M": 3,
    "MEDIUM": 3,
    "L": 4,
    "LARGE": 4,
    "XL": 5,
    "XLARGE": 5,
    "XXL": 6,
    "2XL": 6,
    "XXXL": 7,
    "3XL": 7,
}
_UK_TOP_RANGES = (
    (4, 6, 1),
    (8, 10, 2),
    (12, 14, 3),
    (16, 18, 4),
    (20, 22, 5),
)


def item_box_from_raw_json(raw_json: Any) -> dict[str, Any] | None:
    if isinstance(raw_json, str):
        try:
            raw_json = json.loads(raw_json)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw_json, dict):
        return None
    item_box = raw_json.get("item_box")
    return item_box if isinstance(item_box, dict) else None


def item_box_accessibility_label(raw_json: Any) -> str:
    item_box = item_box_from_raw_json(raw_json)
    return str((item_box or {}).get("accessibility_label") or "")


def item_box_first_line(raw_json: Any) -> str | None:
    item_box = item_box_from_raw_json(raw_json)
    first_line = str((item_box or {}).get("first_line") or "").strip()
    return first_line or None


@lru_cache(maxsize=65536)
def listing_size_from_label(label: str) -> str | None:
    match = _LABEL_SIZE.search(label)
    if not match:
        return None
    size = match.group(1).strip()
    return size or None


@lru_cache(maxsize=65536)
def _size_from_title(title: str) -> str | None:
    match = _TITLE_SIZE.search(title)
    if match:
        return match.group("size").strip()
    match = _TITLE_WAIST.search(title)
    if match:
        return match.group(0).strip()
    match = _PAREN_SIZE.search(title)
    return match.group(1).strip() if match else None


@lru_cache(maxsize=65536)
def _parse_top_size(value: str) -> int | None:
    text = value.strip()
    match = _TOP_LABEL.search(text.upper())
    if match:
        token = re.sub(r"[\s-]+", "", match.group("size").upper())
        return _TOP_SIZE_INDEX.get(token)
    match = _UK_TOP_RANGE.search(text)
    if match:
        low, high = map(int, match.groups())
        midpoint = (low + high) // 2
        return _uk_top_index(midpoint)
    match = _TOP_NUMBER.search(text)
    if match:
        return _uk_top_index(int(match.group(1)))
    return None


def _uk_top_index(size: int) -> int | None:
    for low, high, index in _UK_TOP_RANGES:
        if low <= size <= high:
            return index
    return None


@lru_cache(maxsize=65536)
def _parse_waist_size(value: str) -> int | None:
    text = value.strip()
    for pattern in (_WAIST_W_PREFIX, _WAIST_W_SUFFIX, _WAIST_PAIR, _WAIST_NAMED):
        match = pattern.search(text)
        if match:
            return int(match.group(1))
    match = _WAIST_NUMBER.search(text)
    if match:
        size = int(match.group(1))
        return size if 24 <= size <= 44 else None
    return None


@lru_cache(maxsize=65536)
def _parse_shoe_size(value: str) -> float | None:
    text = value.strip()
    match = _UK_SHOE_SIZE.search(text)
    if match:
        size = float(match.group(1))
    else:
        match = _EU_SHOE_SIZE.search(text)
        if match:
            size = float(match.group(1))
        else:
            match = _SHOE_NUMBER.search(text)
            if not match:
                return None
            size = float(match.group(1))
    if size >= 34:
        size = round((size - 33.5) * 2) / 2
    return size


def _within_one_top_step(listing_size: str, user_size: str) -> bool | None:
    listing_index = _parse_top_size(listing_size)
    user_index = _parse_top_size(user_size)
    if listing_index is None or user_index is None:
        return None
    return abs(listing_index - user_index) <= 1


def _text_size(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def fits_brief(item: dict[str, Any], sizes: dict[str, Any]) -> bool:
    if (
        item.get("source") != "vinted"
        and item.get("condition_hint") != "second-hand"
    ):
        return True

    size = _text_size(item.get("size")) or _size_from_title(
        _text_size(item.get("title"))
    )
    if not size or _ONE_SIZE.search(size):
        return True

    slot = str(tags_for(item).get("slot") or "").strip().lower()
    size_key = {
        "outerwear": "tops",
        "top": "tops",
        "knitwear": "tops",
        "dress": "tops",
        "bottom": "waist",
        "shoes": "shoes",
    }.get(slot)
    if size_key is None:
        return True

    user_size = _text_size((sizes or {}).get(size_key))
    if not user_size:
        return True

    if size_key == "tops":
        fits = _within_one_top_step(size, user_size)
        return True if fits is None else fits

    if size_key == "waist":
        listing_waist = _parse_waist_size(size)
        if listing_waist is not None:
            user_waist = _parse_waist_size(user_size)
            return (
                True
                if user_waist is None
                else abs(listing_waist - user_waist) <= 1
            )
        fits = _within_one_top_step(size, _text_size((sizes or {}).get("tops")))
        return True if fits is None else fits

    listing_shoe = _parse_shoe_size(size)
    user_shoe = _parse_shoe_size(user_size)
    return (
        True
        if listing_shoe is None or user_shoe is None
        else abs(listing_shoe - user_shoe) <= 0.5
    )
