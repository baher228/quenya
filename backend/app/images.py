import base64
import hashlib
import io
from pathlib import Path
from threading import get_ident

import httpx
from PIL import Image, ImageOps

from app.config import BACKEND_ROOT

IMAGE_DIR = BACKEND_ROOT / "data" / "img"
BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)


def image_key(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()


def image_path(url: str) -> Path:
    return IMAGE_DIR / f"{image_key(url)}.jpg"


def cached_image_path(key: str) -> Path:
    if len(key) != 40 or any(char not in "0123456789abcdef" for char in key):
        raise ValueError("Invalid image cache key")
    return IMAGE_DIR / f"{key}.jpg"


def image_data_url(url: str) -> str:
    destination = image_path(url)
    if not destination.exists():
        IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        response = httpx.get(
            url,
            headers={"User-Agent": BROWSER_UA},
            timeout=15,
            follow_redirects=True,
        )
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
