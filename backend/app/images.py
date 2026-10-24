import base64
import hashlib
import io
from pathlib import Path
from threading import get_ident
from urllib.parse import urlsplit, urlunsplit

import httpx
from PIL import Image, ImageOps

from app.config import BACKEND_ROOT

IMAGE_DIR = BACKEND_ROOT / "data" / "img"
BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)
ASOS_IMAGE_HOST = "images.asos-media.com"
ASOS_IMAGE_QUERY = "$n_640w$&wid=640&fit=constrain"
ASOS_IMAGE_UA = "curl/8.5.0"


def normalize_image_url(url: str) -> str:
    parts = urlsplit(url)
    if (parts.hostname or "").lower() != ASOS_IMAGE_HOST:
        return url
    return urlunsplit((parts.scheme, parts.netloc, parts.path, ASOS_IMAGE_QUERY, ""))


def image_key(url: str) -> str:
    return hashlib.sha1(normalize_image_url(url).encode("utf-8")).hexdigest()


def image_path(url: str) -> Path:
    return IMAGE_DIR / f"{image_key(url)}.jpg"


def cached_image_path(key: str) -> Path:
    if len(key) != 40 or any(char not in "0123456789abcdef" for char in key):
        raise ValueError("Invalid image cache key")
    return IMAGE_DIR / f"{key}.jpg"


def image_data_url(url: str) -> str:
    normalized_url = normalize_image_url(url)
    destination = image_path(normalized_url)
    if not destination.exists():
        IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        is_asos_image = (urlsplit(normalized_url).hostname or "").lower() == ASOS_IMAGE_HOST
        user_agent = ASOS_IMAGE_UA if is_asos_image else BROWSER_UA
        for attempt in range(2 if is_asos_image else 1):
            try:
                response = httpx.get(
                    normalized_url,
                    headers={"User-Agent": user_agent},
                    timeout=15,
                    follow_redirects=True,
                )
                break
            except httpx.TimeoutException:
                if attempt == 1 or not is_asos_image:
                    raise
        response.raise_for_status()
        with Image.open(io.BytesIO(response.content)) as original:
            image = ImageOps.exif_transpose(original).convert("RGB")
            image.thumbnail((512, 512), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=80, optimize=True)
        temporary = destination.with_name(f"{destination.stem}.{get_ident()}.tmp")
        temporary.write_bytes(buffer.getvalue())
        temporary.replace(destination)
    encoded = base64.b64encode(destination.read_bytes()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"
