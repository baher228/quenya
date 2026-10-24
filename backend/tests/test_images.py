from io import BytesIO
from types import SimpleNamespace

import httpx
from PIL import Image

from app import images


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
