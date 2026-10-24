import base64
import hashlib
import io
import os
from pathlib import Path
from threading import get_ident
from urllib.parse import quote, urlsplit, urlunsplit

import httpx
from PIL import Image, ImageOps

from app.config import BACKEND_ROOT, SPORT_IMAGE_UA

IMAGE_DIR = BACKEND_ROOT / "data" / "img"
UPLOAD_DIR = BACKEND_ROOT / "data" / "uploads"
STORAGE_IMAGE_DIR = Path("/tmp/quenya-img")
STORAGE_BUCKET = "quenya-img"
BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)
ASOS_IMAGE_HOST = "images.asos-media.com"
ASOS_IMAGE_QUERY = "$n_640w$&wid=640&fit=constrain"
ASOS_IMAGE_UA = "curl/8.5.0"
SPORT_IMAGE_DOMAINS = (
    "gymshark.com",
    "nike.com",
    "underarmour.co.uk",
    "lululemon.co.uk",
    "decathlon.co.uk",
    "sportsdirect.com",
    "cotswoldoutdoor.com",
    "arcteryx.com",
    "montane.com",
    "rab.equipment",
    "ellis-brigham.com",
    "patagonia.com",
)


def normalize_image_url(url: str) -> str:
    parts = urlsplit(url)
    if (parts.hostname or "").lower() != ASOS_IMAGE_HOST:
        return url
    return urlunsplit((parts.scheme, parts.netloc, parts.path, ASOS_IMAGE_QUERY, ""))


def storage_enabled() -> bool:
    return bool(
        os.getenv("QUENYA_IMAGE_STORE") == "supabase"
        and os.getenv("SUPABASE_URL")
        and os.getenv("SUPABASE_SECRET_KEY")
    )


def _image_dir() -> Path:
    return STORAGE_IMAGE_DIR if storage_enabled() else IMAGE_DIR


def _storage_credentials() -> tuple[str, str]:
    base_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    secret = os.getenv("SUPABASE_SECRET_KEY", "")
    if not base_url or not secret:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SECRET_KEY are required")
    return base_url, secret


def storage_object_url(path: str) -> str:
    base_url, _secret = _storage_credentials()
    return (
        f"{base_url}/storage/v1/object/{STORAGE_BUCKET}/"
        f"{quote(path, safe='/')}"
    )


def storage_public_url(path: str) -> str:
    base_url, _secret = _storage_credentials()
    return (
        f"{base_url}/storage/v1/object/public/{STORAGE_BUCKET}/"
        f"{quote(path, safe='/')}"
    )


def _storage_headers(secret: str, *, content_type: str | None = None) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {secret}",
        "apikey": secret,
    }
    if content_type:
        headers["Content-Type"] = content_type
        headers["x-upsert"] = "true"
    return headers


def download_storage_object(path: str) -> bytes:
    _base_url, secret = _storage_credentials()
    response = httpx.get(
        storage_object_url(path),
        headers=_storage_headers(secret),
        follow_redirects=True,
        timeout=15,
    )
    response.raise_for_status()
    return response.content


def upload_storage_object(path: str, content: bytes, content_type: str) -> None:
    _base_url, secret = _storage_credentials()
    response = httpx.post(
        storage_object_url(path),
        content=content,
        headers=_storage_headers(secret, content_type=content_type),
        timeout=15,
    )
    response.raise_for_status()


def save_upload(key: str, content: bytes) -> str:
    if storage_enabled():
        storage_path = f"uploads/{key}.jpg"
        upload_storage_object(storage_path, content, "image/jpeg")
        return storage_path
    path = UPLOAD_DIR / f"{key}.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return str(path)


def read_upload(path: str) -> bytes:
    if storage_enabled():
        if path.startswith("uploads/"):
            return download_storage_object(path)
        local_path = Path(path)
        if not local_path.is_file():
            return download_storage_object(f"uploads/{local_path.name}")
    return Path(path).read_bytes()


def image_key(url: str) -> str:
    return hashlib.sha1(normalize_image_url(url).encode("utf-8")).hexdigest()


def image_path(url: str) -> Path:
    return _image_dir() / f"{image_key(url)}.jpg"


def cached_image_path(key: str) -> Path:
    if len(key) != 40 or any(char not in "0123456789abcdef" for char in key):
        raise ValueError("Invalid image cache key")
    return _image_dir() / f"{key}.jpg"


def _write_image(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.stem}.{get_ident()}.tmp")
    temporary.write_bytes(content)
    temporary.replace(path)


def image_data_url(url: str) -> str:
    normalized_url = normalize_image_url(url)
    destination = image_path(normalized_url)
    if not destination.exists():
        content = None
        if storage_enabled():
            try:
                content = download_storage_object(f"{image_key(normalized_url)}.jpg")
            except httpx.HTTPError:
                pass
            if content is not None:
                _write_image(destination, content)
        if content is None:
            content = _fetch_image(normalized_url)
            _write_image(destination, content)
            if storage_enabled():
                upload_storage_object(
                    f"{image_key(normalized_url)}.jpg", content, "image/jpeg"
                )
    encoded = base64.b64encode(destination.read_bytes()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _fetch_image(normalized_url: str) -> bytes:
    is_asos_image = (urlsplit(normalized_url).hostname or "").lower() == ASOS_IMAGE_HOST
    host = (urlsplit(normalized_url).hostname or "").lower()
    is_sport_image = any(
        host == domain or host.endswith(f".{domain}")
        for domain in SPORT_IMAGE_DOMAINS
    )
    user_agent = (
        ASOS_IMAGE_UA
        if is_asos_image
        else SPORT_IMAGE_UA
        if is_sport_image
        else BROWSER_UA
    )
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
    return buffer.getvalue()
