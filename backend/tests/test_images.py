from io import BytesIO
from types import SimpleNamespace

import httpx
from PIL import Image

from app import images


def _image_bytes(size):
    buffer = BytesIO()
    Image.new("RGB", size, "navy").save(buffer, format="PNG")
    return buffer.getvalue()


def test_encode_display_jpeg_downscales_and_preserves_smaller_dimensions():
    large = images.encode_display_jpeg(_image_bytes((3000, 4000)))
    small = images.encode_display_jpeg(_image_bytes((800, 1000)))

    with Image.open(BytesIO(large)) as encoded_large:
        assert encoded_large.format == "JPEG"
        assert encoded_large.size == (1200, 1600)
    with Image.open(BytesIO(small)) as encoded_small:
        assert encoded_small.format == "JPEG"
        assert encoded_small.size == (800, 1000)


def test_upscale_image_url_rewrites_only_verified_cdn_variants():
    burberry = (
        "https://assets.burberry.com/is/image/Burberryltd/item"
        "?wid=640&hei=852&fmt=jpg"
    )
    ssense = "https://img.ssensemedia.com/h_960,w_960/item.jpg?foo=bar"

    assert images.upscale_image_url(burberry) == (
        "https://assets.burberry.com/is/image/Burberryltd/item"
        "?wid=1600&hei=2133&fmt=jpg"
    )
    assert images.upscale_image_url(ssense) == (
        "https://img.ssensemedia.com/h_1600,w_1600/item.jpg?foo=bar"
    )
    unknown = "https://images.example.test/item_100x100.jpg?width=100"
    assert images.upscale_image_url(unknown) == unknown


def test_asos_image_download_normalizes_url_and_retries_timeout(monkeypatch, tmp_path):
    source_url = (
        "https://images.asos-media.com/products/jacket/123456789-1-black"
        "?$n_240w$&wid=44&fit=constrain"
    )
    expected_url = (
        "https://images.asos-media.com/products/jacket/123456789-1-black"
        "?$n_640w$&wid=640&fit=constrain"
    )
    buffer = BytesIO()
    Image.new("RGB", (2, 2), "navy").save(buffer, format="JPEG")
    response = SimpleNamespace(content=buffer.getvalue(), raise_for_status=lambda: None)
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        if len(calls) == 1:
            raise httpx.ReadTimeout("read timed out")
        return response

    monkeypatch.setattr(images, "IMAGE_DIR", tmp_path)
    monkeypatch.setattr(images.httpx, "get", fake_get)

    result = images.image_data_url(source_url)

    assert result.startswith("data:image/jpeg;base64,")
    assert len(calls) == 2
    assert all(call[0] == expected_url for call in calls)
    assert all(call[1]["headers"]["User-Agent"] == "curl/8.5.0" for call in calls)
    assert all(call[1]["timeout"] == 15 for call in calls)


def test_download_display_image_falls_back_to_original(monkeypatch):
    original = (
        "https://assets.burberry.com/is/image/Burberryltd/item"
        "?wid=640&hei=852"
    )
    calls = []

    class Response:
        def __init__(self, content, status=200):
            self.content = content
            self.status = status

        def raise_for_status(self):
            if self.status >= 400:
                raise httpx.HTTPStatusError(
                    "bad status",
                    request=httpx.Request("GET", "https://example.test"),
                    response=httpx.Response(self.status),
                )

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        if len(calls) == 1:
            return Response(b"missing", status=404)
        return Response(_image_bytes((20, 30)))

    monkeypatch.setattr(images.httpx, "get", fake_get)
    content = images.download_display_image(original, referer="https://shop.example/item")

    assert content == _image_bytes((20, 30))
    assert len(calls) == 2
    assert "wid=1600" in calls[0][0]
    assert calls[1][0] == original
    assert calls[0][1]["headers"]["Referer"] == "https://shop.example/item"


def _jpeg_bytes():
    buffer = BytesIO()
    Image.new("RGB", (4, 4), "navy").save(buffer, format="JPEG")
    return buffer.getvalue()


def test_storage_image_uses_local_then_storage_then_origin(monkeypatch, tmp_path):
    monkeypatch.setenv("QUENYA_IMAGE_STORE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://project.example")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "test-secret")
    monkeypatch.setattr(images, "STORAGE_IMAGE_DIR", tmp_path)

    source_url = "https://shop.example/item.jpg"
    key = images.image_key(source_url)
    local_path = images.cached_image_path(key)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(_jpeg_bytes())
    calls = []
    uploads = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        if "/storage/v1/object/" in url:
            return httpx.Response(404, request=httpx.Request("GET", url))
        return httpx.Response(200, content=_jpeg_bytes(), request=httpx.Request("GET", url))

    def fake_post(url, **kwargs):
        uploads.append((url, kwargs))
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(images.httpx, "get", fake_get)
    monkeypatch.setattr(images.httpx, "post", fake_post)

    assert images.image_data_url(source_url).startswith("data:image/jpeg;base64,")
    assert calls == []

    local_path.unlink()
    assert images.image_data_url(source_url).startswith("data:image/jpeg;base64,")
    assert [url.split("/storage/v1/object/")[-1] for url, _ in calls] == [
        f"quenya-img/{key}.jpg",
        "https://shop.example/item.jpg",
    ]
    assert uploads[0][0].endswith(f"/quenya-img/{key}.jpg")
    assert uploads[0][1]["headers"]["x-upsert"] == "true"


def test_storage_download_is_cached_and_uploads_support_read_write(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("QUENYA_IMAGE_STORE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://project.example")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "test-secret")
    monkeypatch.setattr(images, "STORAGE_IMAGE_DIR", tmp_path / "cache")
    calls = []
    content = _jpeg_bytes()

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(200, content=content, request=httpx.Request("GET", url))

    monkeypatch.setattr(images.httpx, "get", fake_get)
    monkeypatch.setattr(
        images.httpx,
        "post",
        lambda url, **kwargs: httpx.Response(
            200, request=httpx.Request("POST", url)
        ),
    )

    source_url = "https://shop.example/cached.jpg"
    images.image_data_url(source_url)
    assert len(calls) == 1
    assert "/storage/v1/object/quenya-img/" in calls[0][0]
    images.image_data_url(source_url)
    assert len(calls) == 1

    upload_path = images.save_upload("upload-1", content)
    assert upload_path == "uploads/upload-1.jpg"
    assert images.read_upload(upload_path) == content
    assert images.read_upload("/old/local/path/upload-1.jpg") == content
    assert calls[-1][0].endswith("/quenya-img/uploads/upload-1.jpg")


def test_storage_requires_explicit_mode_and_img_route_redirects(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("SUPABASE_URL", "https://project.example")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "test-secret")
    monkeypatch.delenv("QUENYA_IMAGE_STORE", raising=False)
    assert images.storage_enabled()

    monkeypatch.setenv("QUENYA_IMAGE_STORE", "supabase")
    from app.main import get_image

    key = "a" * 40
    response = get_image(f"{key}.jpg")
    assert response.status_code == 302
    assert response.headers["location"] == (
        "https://project.example/storage/v1/object/public/"
        f"quenya-img/{key}.jpg"
    )
    monkeypatch.setenv("QUENYA_IMAGE_STORE", "r2")
    monkeypatch.setenv("QUENYA_R2_PUBLIC_URL", "https://pub.example/")
    response = get_image(f"{key}.jpg")
    assert response.status_code == 302
    assert response.headers["location"] == f"https://pub.example/{key}.jpg"


def test_r2_upload_uses_configured_bucket_and_cache_headers(monkeypatch):
    import boto3

    calls = []
    client_options = []

    class StubClient:
        def put_object(self, **kwargs):
            calls.append(kwargs)

    def fake_client(service, **kwargs):
        client_options.append((service, kwargs))
        return StubClient()

    monkeypatch.setattr(boto3, "client", fake_client)
    monkeypatch.setenv("QUENYA_R2_ACCOUNT_ID", "account")
    monkeypatch.setenv("QUENYA_R2_ACCESS_KEY_ID", "access")
    monkeypatch.setenv("QUENYA_R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("QUENYA_R2_BUCKET", "quenya")
    images._cached_r2_client.cache_clear()
    try:
        images.upload_r2_object("a" * 40 + ".jpg", b"jpeg")
    finally:
        images._cached_r2_client.cache_clear()

    assert client_options == [
        (
            "s3",
            {
                "endpoint_url": "https://account.r2.cloudflarestorage.com",
                "aws_access_key_id": "access",
                "aws_secret_access_key": "secret",
                "region_name": "auto",
            },
        )
    ]
    assert calls == [
        {
            "Bucket": "quenya",
            "Key": "a" * 40 + ".jpg",
            "Body": b"jpeg",
            "ContentType": "image/jpeg",
            "CacheControl": "public, max-age=2592000",
        }
    ]


def test_user_uploads_still_use_supabase_in_r2_mode(monkeypatch):
    monkeypatch.setenv("QUENYA_IMAGE_STORE", "r2")
    monkeypatch.setenv("SUPABASE_URL", "https://project.example")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "test-secret")
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(images.httpx, "post", fake_post)

    result = images.save_upload("inspiration", b"photo")

    assert result == "uploads/inspiration.jpg"
    assert calls[0][0].endswith(
        "/storage/v1/object/quenya-img/uploads/inspiration.jpg"
    )


def test_local_uploads_remain_on_disk(monkeypatch, tmp_path):
    monkeypatch.setenv("QUENYA_IMAGE_STORE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://project.example")
    monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)
    monkeypatch.setattr(images, "UPLOAD_DIR", tmp_path)

    path = images.save_upload("upload-2", b"local bytes")

    assert images.storage_enabled() is False
    assert images.read_upload(path) == b"local bytes"
