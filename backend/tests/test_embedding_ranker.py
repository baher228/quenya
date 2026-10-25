import hashlib
from pathlib import Path

import numpy as np
import pytest

from app.clip_tokenizer import SimpleTokenizer
from app.embedding_ranker import (
    blend_score,
    choose_bootdiv,
    fallback_arm,
    rank_candidates,
)
from app.rank import rank_items


def _item(item_id, vector=None, score=0):
    return {
        "id": item_id,
        "price_gbp": 20,
        "availability": "InStock",
        "source": "shop.example",
        "tags": {
            "slot": "top",
            "gender": "unisex",
            "condition": "new",
            "aesthetics": [],
            "colours": [],
            "materials": [],
            "niche": 0,
        },
        "score": score,
    }


def test_blend_matches_direct_numpy_formula(monkeypatch):
    from app import embedding_ranker

    monkeypatch.setattr(embedding_ranker, "score_item", lambda _item, _taste: 2.0)
    catalogue = np.asarray([[1, 0], [0, 1], [-1, 0], [0, -1]], dtype=np.float32)
    brief = np.asarray([1, 0], dtype=np.float32)
    history = np.asarray([0, 1], dtype=np.float32)
    vector = np.asarray([0.6, 0.8], dtype=np.float32)
    brief_z = (vector @ brief - (catalogue @ brief).mean()) / (catalogue @ brief).std()
    history_z = (
        vector @ history - (catalogue @ history).mean()
    ) / (catalogue @ history).std()
    expected = 8 / 10 * brief_z + 2 / 10 * history_z + 0.5
    actual = blend_score(
        vector, brief, history, 2, {}, _item("x"), catalogue
    )
    assert actual == pytest.approx(float(expected))


def test_ranker_uses_only_the_passed_board_history():
    catalogue = np.asarray([[1, 0], [0, 1], [-1, 0], [0, -1]], dtype=np.float32)
    items = [_item("a"), _item("b")]
    embeddings = {
        "a": {"mix": np.asarray([1, 0], dtype=np.float32)},
        "b": {"mix": np.asarray([0, 1], dtype=np.float32)},
        "other-board": {"mix": np.asarray([-1, 0], dtype=np.float32)},
    }
    board_a = [{"item_id": "a", "liked": True}]
    first = rank_candidates(items, "board-a", {}, None, board_a, embeddings, catalogue)
    embeddings["other-board"]["mix"] = np.asarray([-1, 0], dtype=np.float32)
    second = rank_candidates(items, "board-a", {}, None, board_a, embeddings, catalogue)
    assert [item["id"] for item in first[0]] == [item["id"] for item in second[0]]
    assert first[1] == second[1]


def test_out_of_stock_items_are_not_ranked_in_tags_or_embed_arm():
    brief = {"gender": "both", "budget_gbp": 100, "looking_for": "pieces"}
    profile = {"price_ceiling_gbp": 100, "condition_preference": "both"}
    items = [_item("available"), {**_item("sold"), "availability": "OutOfStock"}]
    ranked = rank_items(items, brief, profile)
    assert [item["id"] for item in ranked] == ["available"]
    embed_ranked, _scores, _history = rank_candidates(
        ranked, "board", {}, None, [], {}, np.zeros((0, 2), dtype=np.float32)
    )
    assert [item["id"] for item in embed_ranked] == ["available"]


def test_profile_arm_is_stable_and_sha1_parity_based():
    profile_id = "board-a"
    expected = "embed" if hashlib.sha1(profile_id.encode()).digest()[-1] & 1 else "tags"
    assert fallback_arm(profile_id, "ab") == expected
    assert fallback_arm(profile_id, "ab") == fallback_arm(profile_id, "ab")
    assert fallback_arm(profile_id, "tags") == "tags"
    assert fallback_arm(profile_id, "embed") == "embed"


def test_profile_ranker_arm_is_persisted(monkeypatch, tmp_path):
    from app import db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "ranker.sqlite")
    db._INITIALIZED_DATABASES.clear()
    db.create_profile("board", {"text": "workwear"}, ranker_arm="embed")
    assert db.get_or_assign_ranker_arm("board", "tags") == "embed"
    db._INITIALIZED_DATABASES.clear()


def test_swipes_and_brief_vectors_are_loaded_per_board(monkeypatch, tmp_path):
    from app import db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "boards.sqlite")
    db._INITIALIZED_DATABASES.clear()
    db.create_profile("board-a", {"text": "workwear"}, ranker_arm="tags")
    db.create_profile("board-b", {"text": "workwear"}, ranker_arm="embed")
    db.insert_swipe("board-a", "item-a", True)
    db.insert_swipe("board-b", "item-b", True)
    vector_a = np.zeros(512, dtype=np.float32)
    vector_b = np.zeros(512, dtype=np.float32)
    vector_a[0] = 1
    vector_b[1] = 1
    db.store_profile_embedding(
        "board-a", "clip32", vector_a, "a"
    )
    db.store_profile_embedding(
        "board-b", "clip32", vector_b, "b"
    )
    db.store_item_embedding("item-a", "clip32", vector_a, vector_a, vector_a)
    db.store_item_embedding("item-b", "clip32", vector_b, vector_b, vector_b)
    assert db.profile_swipes("board-a") == [{"item_id": "item-a", "liked": True}]
    np.testing.assert_array_equal(db.profile_embedding("board-a"), vector_a)
    np.testing.assert_array_equal(db.profile_embedding("board-b"), vector_b)
    np.testing.assert_array_equal(db.item_embedding_map()["item-a"]["mix"], vector_a)
    mix_vectors = db.item_mix_vectors(["item-a", "missing"])
    np.testing.assert_array_equal(mix_vectors["item-a"]["mix"], vector_a)
    nearest = db.nearest_item_mix(vector_a, ["item-b", "item-a"], k=1)
    assert nearest[0][0] == "item-a"
    np.testing.assert_array_equal(nearest[0][1], vector_a)
    assert len(db.random_item_mix_vectors(1)) == 1
    db._INITIALIZED_DATABASES.clear()


def test_no_brief_vector_history_only_and_items_without_vectors_fall_back_safely():
    catalogue = np.asarray([[1, 0], [0, 1], [-1, 0]], dtype=np.float32)
    items = [_item("a"), _item("b")]
    embeddings = {"a": {"mix": np.asarray([1, 0], dtype=np.float32)}}
    ordered, scores, history = rank_candidates(
        items, "board", {}, None,
        [{"item_id": "a", "liked": True}], embeddings, catalogue,
    )
    assert history is not None
    assert np.isfinite(scores["b"])
    assert {item["id"] for item in ordered} == {"a", "b"}
    no_vectors, _scores, history = rank_candidates(
        items, "board", {}, None, [], {}, catalogue
    )
    assert [item["id"] for item in no_vectors] == ["a", "b"]
    assert history is None


def test_seeded_bootstrap_is_repeatable_and_can_depart_from_greedy():
    from app import embedding_ranker

    items = [_item("brief"), _item("history")]
    embeddings = {
        "brief": {"mix": np.asarray([1, 0], dtype=np.float32)},
        "history": {"mix": np.asarray([0, 1], dtype=np.float32)},
    }
    sample = np.asarray([[1, 0], [-1, 0], [0, 1], [0, -1]], dtype=np.float32)
    swipes = [{"item_id": "history", "liked": True}]
    monkeypatch_scores = embedding_ranker.score_item
    embedding_ranker.score_item = lambda _item, _taste: 0.0
    try:
        greedy = "brief"
        pick = choose_bootdiv(
                items, [], swipes, embeddings, {"brief": 1, "history": 0},
            np.asarray([1, 0], dtype=np.float32), {}, sample, [], np.random.default_rng(1),
        )["id"]
        repeat = choose_bootdiv(
            items, [], swipes, embeddings, {"brief": 1, "history": 0},
            np.asarray([1, 0], dtype=np.float32), {}, sample, [], np.random.default_rng(1),
        )["id"]
    finally:
        embedding_ranker.score_item = monkeypatch_scores
    assert pick == repeat
    assert pick != greedy


def test_brief_text_falls_back_to_string_and_list_values():
    from app.embedding import brief_text

    assert brief_text({"text": "  ", "style": ["workwear", "dark"], "note": "daily"}) == (
        "workwear dark daily"
    )


def test_diversity_penalty_avoids_near_duplicate(monkeypatch):
    from app import embedding_ranker

    monkeypatch.setattr(
        embedding_ranker, "score_item",
        lambda item, _taste: {"near": 0.1, "different": 0.0, "low": -4.0}[item["id"]],
    )
    near = _item("near")
    different = _item("different")
    low = _item("low")
    embeddings = {
        "near": {"mix": np.asarray([1, 0], dtype=np.float32)},
        "different": {"mix": np.asarray([0, 1], dtype=np.float32)},
        "low": {"mix": np.asarray([-1, 0], dtype=np.float32)},
    }
    choice = choose_bootdiv(
        [near, different, low], [], [], embeddings,
        {"near": 0.025, "different": 0, "low": -1},
        None, {}, np.zeros((0, 2), dtype=np.float32),
        [np.asarray([1, 0], dtype=np.float32)], np.random.default_rng(1),
    )
    assert choice["id"] == "different"


def test_openclip_tokenizer_parity_when_model_fixture_exists():
    if not Path("/home/ubuntu/research/onnx/text_fp32.onnx").exists():
        pytest.skip("local CLIP ONNX model not present")
    open_clip = pytest.importorskip("open_clip")
    texts = [
        "black Nike Tech Fleece tracksuit",
        "Barbour wax jacket — olive",
        "women's pink ballet flats",
        " ".join(["fashion"] * 100),
    ]
    expected = open_clip.tokenize(texts, context_length=77).cpu().numpy()
    actual = SimpleTokenizer()(texts, context_length=77)
    np.testing.assert_array_equal(actual, expected)
