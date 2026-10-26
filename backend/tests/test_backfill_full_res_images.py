from io import BytesIO

import httpx
from PIL import Image

from app import images
from scripts import backfill_full_res_images as backfill


def _jpeg(size):
    output = BytesIO()
    Image.new("RGB", size, "navy").save(output, format="JPEG")
    return output.getvalue()


def _item():
    return {
        "id": "item-1",
        "source": "shop.example",
        "source_url": "https://shop.example/products/1",
        "image_url": "https://images.example.test/original.jpg",
        "image_path": f"{'a' * 40}.jpg",
        "availability": "InStock",
    }


def _stub_old(monkeypatch, content):
    def download(path):
        assert path == f"{'a' * 40}.jpg"
        if isinstance(content, Exception):
            raise content
        return content

    monkeypatch.setattr(images, "download_storage_object", download)


def test_backfill_uses_upscaled_source_and_uploads_larger_jpeg(monkeypatch):
    calls = []
    uploads = []
    _stub_old(monkeypatch, _jpeg((200, 200)))
    monkeypatch.setattr(
        images, "upscale_image_url", lambda url: url.replace("original", "large")
    )

    def download(url, **kwargs):
        calls.append((url, kwargs))
        return _jpeg((600, 800))

    monkeypatch.setattr(images, "download_image_candidate", download)
    monkeypatch.setattr(
        images, "upload_r2_object", lambda *args: uploads.append(args)
    )

    result = backfill.process_item(_item())

    assert result["status"] == "upgraded"
    assert calls[0][0] == "https://images.example.test/large.jpg"
    assert calls[0][1]["referer"] == "https://shop.example/products/1"
    assert uploads[0][0] == f"{'a' * 40}.jpg"
    assert uploads[0][2] == "image/jpeg"
    assert (result["stored_width"], result["stored_height"]) == (600, 800)


def test_backfill_falls_back_to_original_url(monkeypatch):
    calls = []
    _stub_old(monkeypatch, _jpeg((200, 200)))
    monkeypatch.setattr(
        images, "upscale_image_url", lambda url: url.replace("original", "large")
    )

    def download(url, **kwargs):
        calls.append(url)
        if "large" in url:
            raise httpx.HTTPStatusError(
                "not found",
                request=httpx.Request("GET", url),
                response=httpx.Response(404),
            )
        return _jpeg((400, 400))

    monkeypatch.setattr(images, "download_image_candidate", download)
    monkeypatch.setattr(images, "upload_r2_object", lambda *args: None)

    result = backfill.process_item(_item())

    assert calls == [
        "https://images.example.test/large.jpg",
        "https://images.example.test/original.jpg",
    ]
    assert result["status"] == "upgraded"


def test_backfill_keeps_and_copies_old_object_when_source_fails(monkeypatch):
    old = _jpeg((300, 400))
    uploaded = []
    _stub_old(monkeypatch, old)
    monkeypatch.setattr(images, "upscale_image_url", lambda url: url)

    def fail_download(url, **kwargs):
        raise httpx.HTTPStatusError(
            "not found",
            request=httpx.Request("GET", url),
            response=httpx.Response(404),
        )

    monkeypatch.setattr(images, "download_image_candidate", fail_download)
    monkeypatch.setattr(
        images, "upload_r2_object", lambda key, content, content_type: uploaded.append(
            (key, content, content_type)
        )
    )

    result = backfill.process_item(_item())

    assert result["status"] == "kept_old"
    assert uploaded == [(f"{'a' * 40}.jpg", old, "image/jpeg")]


def test_backfill_counts_missing_when_source_and_supabase_object_are_absent(
    monkeypatch, caplog
):
    response = httpx.Response(404)
    _stub_old(
        monkeypatch,
        httpx.HTTPStatusError(
            "not found",
            request=httpx.Request("GET", "https://project.example/object"),
            response=response,
        ),
    )
    monkeypatch.setattr(images, "upscale_image_url", lambda url: url)

    def fail_download(url, **kwargs):
        raise httpx.HTTPStatusError(
            "not found",
            request=httpx.Request("GET", url),
            response=httpx.Response(404),
        )

    monkeypatch.setattr(images, "download_image_candidate", fail_download)

    result = backfill.process_item(_item())

    assert result["status"] == "missing"
    assert "item id=item-1 source=shop.example" in caplog.text
