from types import SimpleNamespace

from app import db, phase2_api
from app.schemas import LiveSearchRequest, RouteOut


def _record(item_id):
    return {
        "id": item_id,
        "source": "vinted",
        "source_url": f"https://www.vinted.co.uk/items/{item_id}",
        "title": "Canvas jacket",
        "price_gbp": 20.0,
        "image_url": f"https://images.example/{item_id}.jpg",
    }


def test_failed_items_are_skipped_by_default_and_explicitly_retryable(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "items.sqlite")
    db.insert_items(
        [_record("clean"), _record("failed"), _record("tagged")]
    )
    db.save_tag_error("failed", "previous Mubit failure")
    db.save_tags("tagged", {"slot": "top"}, "mreq-tag")

    assert {item["id"] for item in db.untagged_items(10)} == {"clean"}
    assert {item["id"] for item in db.untagged_items(10, retry_failed=True)} == {
        "clean",
        "failed",
    }


def test_live_search_does_not_retag_previously_failed_items(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "live.sqlite")
    item = _record("failed")
    db.insert_items([item])
    db.save_tag_error(item["id"], "previous Mubit failure")
    monkeypatch.setattr(
        db, "active_profile", lambda _kind: {"id": "profile", "brief": {"gender": "both"}}
    )
    monkeypatch.setattr(
        phase2_api.vinted, "search", lambda *_args, **_kwargs: [item]
    )
    monkeypatch.setattr(phase2_api.retail, "search", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        phase2_api.catalog, "crawl", lambda *_args, **_kwargs: ([], {"tavily_urls_used": 0})
    )
    route = RouteOut(context="city", board_id=None, label="City", confidence=0.9, reason="test")
    monkeypatch.setattr(
        phase2_api,
        "route_input",
        lambda *_args: (route, None, SimpleNamespace(request_id="mreq-test"), "content"),
    )

    def unexpected_tag(*_args, **_kwargs):
        raise AssertionError("previously failed item was sent to Mubit again")

    monkeypatch.setattr(phase2_api, "tag_item", unexpected_tag)

    response = phase2_api.live_search(LiveSearchRequest(q="canvas jacket"))

    assert response["items"] == []
