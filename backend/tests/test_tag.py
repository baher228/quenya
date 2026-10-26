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
    db.save_tag_error("failed", "previous provider failure")
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
    db.save_tag_error(item["id"], "previous provider failure")
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
        raise AssertionError("previously failed item was sent for tagging again")

    monkeypatch.setattr(phase2_api, "tag_item_with_vectors", unexpected_tag)

    response = phase2_api.live_search(LiveSearchRequest(q="canvas jacket"))

    assert response["items"] == []


def test_live_search_persists_local_embedding_with_tags(monkeypatch, tmp_path):
    import numpy as np

    from app.llm import CallMeta
    from app.schemas import ItemTags

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "live-local.sqlite")
    db._INITIALIZED_DATABASES.clear()
    db.init_db()
    monkeypatch.setattr(
        phase2_api,
        "CONTEXT_SOURCES",
        {"city": ["vinted"], "other": ["vinted"]},
    )
    monkeypatch.setattr(
        phase2_api,
        "_user_profile",
        lambda: {
            "id": "profile",
            "brief": {"gender": "both", "looking_for": "pieces", "budget_gbp": 80},
        },
    )
    monkeypatch.setattr(
        phase2_api,
        "route_input",
        lambda *_args: (
            RouteOut(
                context="city", board_id=None, label="City",
                confidence=0.9, reason="test",
            ),
            None,
            CallMeta("route-request", "router", 1, None, None),
            "content",
        ),
    )
    item = _record("local-item")
    monkeypatch.setattr(phase2_api.vinted, "search", lambda *_args, **_kwargs: [item])
    monkeypatch.setattr(phase2_api.retail, "search", lambda *_args, **_kwargs: [])
    tags = ItemTags(
        slot="top",
        category="tee",
        colours=["navy"],
        materials=["cotton"],
        fit="regular",
        era="contemporary",
        aesthetics=["streetwear"],
        formality=2,
        niche=0.3,
        gender="unisex",
        logo_heavy=False,
        condition="second-hand",
        one_line="Cotton tee.",
    )
    vectors = (
        np.asarray([1, 0, 0, 0], dtype=np.float32),
        np.asarray([0, 1, 0, 0], dtype=np.float32),
        np.asarray([1, 1, 0, 0], dtype=np.float32),
    )
    monkeypatch.setattr(
        phase2_api,
        "tag_item_with_vectors",
        lambda _item: (tags, CallMeta("local:clip-knn-v1", "clip32", 1, None, None), vectors),
    )

    phase2_api.live_search(LiveSearchRequest(q="canvas jacket"))

    item_row = db.get_item("local-item")
    embeddings = db.item_embedding_map()
    assert item_row["tags_request_id"] == "local:clip-knn-v1"
    np.testing.assert_array_equal(embeddings["local-item"]["img"], vectors[0])
    np.testing.assert_array_equal(embeddings["local-item"]["txt"], vectors[1])
    np.testing.assert_array_equal(embeddings["local-item"]["mix"], vectors[2])
