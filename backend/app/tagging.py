from typing import Any

from app.images import image_data_url, image_path
from app.mubit import CallMeta, call_task
from app.prompts import TAGGER_MODEL, TAGGER_SYSTEM
from app.schemas import ItemTags


def user_content(item: dict[str, Any], note: str | None = None) -> list[dict[str, Any]]:
    text = (
        f"Source: {item['source']}\n"
        f"Title: {item['title']}\n"
        f"Brand: {item.get('brand') or 'unknown'}\n"
        f"Price: £{float(item['price_gbp']):.2f}\n"
        f"Size: {item.get('size') or 'unknown'}\n"
        f"Description: {(item.get('description') or '')[:500] or 'none'}"
    )
    if note:
        text += f"\nReviewer correction: {note}"
    item["image_path"] = str(image_path(item["image_url"]))
    return [
        {"type": "text", "text": text},
        {
            "type": "image_url",
            "image_url": {"url": image_data_url(item["image_url"])},
        },
    ]


def tag_item(
    item: dict[str, Any], note: str | None = None
) -> tuple[ItemTags, CallMeta]:
    content = user_content(item, note)
    parsed, meta = call_task("tagger", TAGGER_SYSTEM, TAGGER_MODEL, content, ItemTags)
    return ItemTags.model_validate(parsed), meta
