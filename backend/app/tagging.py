import base64
import io
import logging
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
import numpy as np
from PIL import Image, ImageOps

from app import images
from app.config import QUENYA_TAGGER
from app.embedding import MODEL_NAME
from app.gender_rules import product_type_for_item, text_gender
from app.llm import CallMeta, call_task
from app.local_tagger import LOCAL_TAGGER_ID, LocalTagger
from app.prompts import TAGGER_MODEL, TAGGER_SYSTEM
from app.schemas import ItemTags

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _local_tagger() -> LocalTagger:
    return LocalTagger.from_db()


def _image_input(item: dict[str, Any]) -> str | Image.Image | None:
    stored_path = item.get("image_path")
    if stored_path:
        path = Path(str(stored_path))
        if path.is_file():
            return str(path)
        if (
            images.display_storage_enabled()
            and not path.is_absolute()
            and not str(stored_path).startswith(("http://", "https://", "data:"))
        ):
            try:
                content = images.download_display_object(str(stored_path))
                with Image.open(io.BytesIO(content)) as opened:
                    return ImageOps.exif_transpose(opened).convert("RGB").copy()
            except (httpx.HTTPError, OSError, RuntimeError):
                if not item.get("image_url"):
                    raise

    image_url = item.get("image_url")
    if not image_url:
        return None
    if image_url.startswith("data:"):
        header, separator, payload = image_url.partition(",")
        if not separator:
            raise ValueError("Invalid image data URL")
        if ";base64" in header:
            content = base64.b64decode(payload, validate=True)
        else:
            from urllib.parse import unquote_to_bytes

            content = unquote_to_bytes(payload)
        with Image.open(io.BytesIO(content)) as opened:
            return ImageOps.exif_transpose(opened).convert("RGB").copy()
    images.image_data_url(image_url)
    cached_path = images.image_path(image_url)
    return str(cached_path) if cached_path.is_file() else None


def _unit(vector: np.ndarray) -> np.ndarray:
    vector = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 1e-12 else vector


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
    item["image_path"] = str(images.image_path(item["image_url"]))
    return [
        {"type": "text", "text": text},
        {
            "type": "image_url",
            "image_url": {"url": images.image_data_url(item["image_url"])},
        },
    ]


def tag_item_with_vectors(
    item: dict[str, Any],
    note: str | None = None,
    *,
    log_attempts: bool = True,
) -> tuple[ItemTags, CallMeta, tuple[np.ndarray | None, np.ndarray, np.ndarray] | None]:
    tagger = str(QUENYA_TAGGER).strip().lower()
    if tagger == "local":
        if note:
            logger.debug("Ignoring reviewer note for local product tagging")
        from app.embedding import encode_images, encode_texts, item_text

        started = time.monotonic()
        image_input = _image_input(item)
        image_vec = encode_images([image_input])[0] if image_input is not None else None
        text_vec = encode_texts([item_text(item)])[0]
        image_unit = _unit(image_vec) if image_vec is not None else None
        text_unit = _unit(text_vec)
        mix_vec = _unit(image_unit + 0.5 * text_unit) if image_unit is not None else text_unit
        tags = _local_tagger().tag(item, image_vec, text_vec)
        meta = CallMeta(
            request_id=LOCAL_TAGGER_ID,
            model_returned=MODEL_NAME,
            latency_ms=round((time.monotonic() - started) * 1000),
            prompt_tokens=None,
            completion_tokens=None,
        )
        return ItemTags.model_validate(tags), meta, (image_vec, text_vec, mix_vec)
    if tagger != "llm":
        raise ValueError("QUENYA_TAGGER must be 'local' or 'llm'")
    content = user_content(item, note)
    parsed, meta = call_task(
        "tagger", TAGGER_SYSTEM, TAGGER_MODEL, content, ItemTags,
        log_attempts=log_attempts,
    )
    tags = ItemTags.model_validate(parsed)
    gender = text_gender(
        item.get("title"),
        product_type_for_item(item),
        item.get("source_url"),
    )
    if gender is not None:
        tags = tags.model_copy(update={"gender": gender})
    return tags, meta, None


def tag_item(
    item: dict[str, Any], note: str | None = None, *, log_attempts: bool = True
) -> tuple[ItemTags, CallMeta]:
    tags, meta, _vectors = tag_item_with_vectors(
        item, note, log_attempts=log_attempts
    )
    return tags, meta
