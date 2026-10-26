import base64
import hashlib
import io
import os
from functools import lru_cache
from pathlib import Path
from threading import get_ident
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import httpx
from PIL import Image, ImageOps

from app.config import (
    BACKEND_ROOT,
    QUENYA_IMAGE_MAX_SIDE,
    QUENYA_IMAGE_QUALITY,
    QUENYA_IMAGE_STORE,
    QUENYA_R2_ACCESS_KEY_ID,
    QUENYA_R2_ACCOUNT_ID,
    QUENYA_R2_BUCKET,
    QUENYA_R2_PUBLIC_URL,
    QUENYA_R2_SECRET_ACCESS_KEY,
    SPORT_IMAGE_UA,
)

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


def encode_display_jpeg(content: bytes) -> bytes:
    with Image.open(io.BytesIO(content)) as original:
        image = ImageOps.exif_transpose(original).convert("RGB")
        image.thumbnail(
            (QUENYA_IMAGE_MAX_SIDE, QUENYA_IMAGE_MAX_SIDE),
            Image.Resampling.LANCZOS,
        )
        output = io.BytesIO()
        image.save(
            output,
            format="JPEG",
            quality=QUENYA_IMAGE_QUALITY,
            optimize=True,
            progressive=True,
        )
    return output.getvalue()


def upscale_image_url(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host == "assets.burberry.com":
        query = parse_qsl(parts.query, keep_blank_values=True)
        if any(key == "wid" and value == "640" for key, value in query) and any(
            key == "hei" and value == "852" for key, value in query
        ):
            query = [
                (
                    key,
                    "1600"
                    if key == "wid" and value == "640"
                    else "2133"
                    if key == "hei" and value == "852"
                    else value,
                )
                for key, value in query
            ]
            return urlunsplit(
                (
                    parts.scheme,
                    parts.netloc,
                    parts.path,
                    urlencode(query, doseq=True),
                    parts.fragment,
                )
            )
    if host == "img.ssensemedia.com" and "h_960,w_960" in parts.path:
        return urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                parts.path.replace("h_960,w_960", "h_1600,w_1600"),
                parts.query,
                parts.fragment,
            )
        )
    return url


def _image_headers(url: str, referer: str | None = None) -> dict[str, str]:
    host = (urlsplit(url).hostname or "").lower()
    is_asos_image = host == ASOS_IMAGE_HOST
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
    headers = {"User-Agent": user_agent}
    if referer:
        headers["Referer"] = referer
    return headers


def download_image_candidate(
    url: str,
    *,
    referer: str | None = None,
    minimum_bytes: int = 0,
) -> bytes:
    normalized_url = normalize_image_url(url)
    headers = _image_headers(normalized_url, referer)
    is_asos_image = (urlsplit(normalized_url).hostname or "").lower() == ASOS_IMAGE_HOST
    for attempt in range(2 if is_asos_image else 1):
        try:
            response = httpx.get(
                normalized_url,
                headers=headers,
                timeout=15,
                follow_redirects=True,
            )
            response.raise_for_status()
            content = response.content
            if len(content) < minimum_bytes:
                raise ValueError("Image download is smaller than expected")
            with Image.open(io.BytesIO(content)) as image:
                image.verify()
            return content
        except httpx.TimeoutException:
            if attempt == 1 or not is_asos_image:
                raise
        except (httpx.HTTPError, OSError, ValueError):
            raise
    raise ValueError("No image URL was available")


def download_display_image(
    url: str,
    *,
    referer: str | None = None,
    minimum_bytes: int = 0,
) -> bytes:
    normalized_url = normalize_image_url(url)
    upscaled_url = upscale_image_url(normalized_url)
    candidates = [upscaled_url]
    if upscaled_url != normalized_url:
        candidates.append(normalized_url)
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            return download_image_candidate(
                candidate,
                referer=referer,
                minimum_bytes=minimum_bytes,
            )
        except (httpx.HTTPError, OSError, ValueError) as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise ValueError("No image URL was available")


def storage_enabled() -> bool:
    return image_store() == "supabase" and supabase_storage_enabled()


def image_store() -> str:
    selected = os.getenv("QUENYA_IMAGE_STORE", QUENYA_IMAGE_STORE).strip().lower()
    selected = selected or "supabase"
    if selected not in {"supabase", "r2"}:
        raise ValueError("QUENYA_IMAGE_STORE must be 'supabase' or 'r2'")
    return selected


def supabase_storage_enabled() -> bool:
    return bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SECRET_KEY"))


def display_storage_enabled() -> bool:
    return image_store() == "r2" or storage_enabled()


def r2_enabled() -> bool:
    return image_store() == "r2"


def _image_dir() -> Path:
    return STORAGE_IMAGE_DIR if display_storage_enabled() else IMAGE_DIR


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


def _r2_credentials() -> tuple[str, str, str]:
    account_id = os.getenv("QUENYA_R2_ACCOUNT_ID", QUENYA_R2_ACCOUNT_ID)
    access_key = os.getenv("QUENYA_R2_ACCESS_KEY_ID", QUENYA_R2_ACCESS_KEY_ID)
    secret_key = os.getenv(
        "QUENYA_R2_SECRET_ACCESS_KEY", QUENYA_R2_SECRET_ACCESS_KEY
    )
    if not account_id or not access_key or not secret_key:
        raise RuntimeError(
            "QUENYA_R2_ACCOUNT_ID, QUENYA_R2_ACCESS_KEY_ID and "
            "QUENYA_R2_SECRET_ACCESS_KEY are required"
        )
    return account_id, access_key, secret_key


def _r2_bucket() -> str:
    return os.getenv("QUENYA_R2_BUCKET", QUENYA_R2_BUCKET) or "quenya"


@lru_cache(maxsize=2)
def _cached_r2_client(account_id: str, access_key: str, secret_key: str):
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
    )


def _r2_client():
    return _cached_r2_client(*_r2_credentials())


def upload_r2_object(path: str, content: bytes, content_type: str = "image/jpeg") -> None:
    try:
        _r2_client().put_object(
            Bucket=_r2_bucket(),
            Key=path,
            Body=content,
            ContentType=content_type,
            CacheControl="public, max-age=2592000",
        )
    except Exception as exc:
        raise RuntimeError(f"R2 upload failed ({type(exc).__name__})") from None


def download_r2_object(path: str) -> bytes:
    try:
        response = _r2_client().get_object(Bucket=_r2_bucket(), Key=path)
        return response["Body"].read()
    except Exception as exc:
        raise RuntimeError(f"R2 download failed ({type(exc).__name__})") from None


def r2_public_url(path: str) -> str:
    public_url = os.getenv("QUENYA_R2_PUBLIC_URL", QUENYA_R2_PUBLIC_URL).rstrip("/")
    if not public_url:
        raise RuntimeError("QUENYA_R2_PUBLIC_URL is required for R2 image redirects")
    return f"{public_url}/{quote(path, safe='/')}"


def download_display_object(path: str) -> bytes:
    if r2_enabled():
        return download_r2_object(path)
    return download_storage_object(path)


def upload_display_object(path: str, content: bytes, content_type: str) -> None:
    if r2_enabled():
        upload_r2_object(path, content, content_type)
    elif storage_enabled():
        upload_storage_object(path, content, content_type)


def persist_display_image(url: str, content: bytes) -> str:
    key = f"{image_key(url)}.jpg"
    if display_storage_enabled():
        upload_display_object(key, content, "image/jpeg")
        return key
    return str(image_path(url))


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
    if supabase_storage_enabled():
        storage_path = f"uploads/{key}.jpg"
        upload_storage_object(storage_path, content, "image/jpeg")
        return storage_path
    path = UPLOAD_DIR / f"{key}.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return str(path)


def read_upload(path: str) -> bytes:
    if supabase_storage_enabled():
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
        if display_storage_enabled():
            try:
                content = download_display_object(
                    f"{image_key(normalized_url)}.jpg"
                )
            except Exception:
                pass
            if content is not None:
                _write_image(destination, content)
        if content is None:
            content = _fetch_image(normalized_url)
            _write_image(destination, content)
            if display_storage_enabled():
                upload_display_object(
                    f"{image_key(normalized_url)}.jpg", content, "image/jpeg"
                )
    encoded = base64.b64encode(destination.read_bytes()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _fetch_image(normalized_url: str) -> bytes:
    return encode_display_jpeg(download_display_image(normalized_url))
