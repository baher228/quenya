import json

import numpy as np
import pytest

from app import db, local_tagger
from app.local_tagger import LocalTagger
from app.schemas import ItemTags


def _tags(**changes):
    tags = {
        "slot": "top",
        "category": "tee",
        "colours": ["black"],
        "materials": ["cotton"],
        "fit": "regular",
        "era": "contemporary",
        "aesthetics": ["streetwear"],
        "formality": 3,
        "niche": 0.4,
        "gender": "menswear",
        "logo_heavy": False,
        "condition": "new",
        "one_line": "tee",
    }
    return {**tags, **changes}


def _tagger(rows, k=10):
    return LocalTagger(
        [(np.asarray(vector, dtype=np.float32), tags) for vector, tags in rows],
        k=k,
    )


def test_weighted_votes_numeric_clamps_and_item_tags_validation(monkeypatch):
    monkeypatch.setattr(
        local_tagger, "_colour_prompt_vectors",
        lambda: np.eye(len(local_tagger.COLOURS), 4, dtype=np.float32),
    )
    tagger = _tagger([
        (
            [1, 0, 0, 0],
            _tags(
                slot="top", fit="slim", era="90s", logo_heavy=True,
                formality=0, niche=-0.2,
                materials=["cotton", "polyester", "wool"],
                aesthetics=["streetwear", "vintage", "minimal", "sporty"],
            ),
        ),
        (
            [0.8, 0.6, 0, 0],
            _tags(
                slot="dress", fit="relaxed", era="2010s", logo_heavy=False,
                formality=0, niche=-0.1, materials=["leather"],
                aesthetics=["bohemian"],
            ),
        ),
    ], k=2)
    result = tagger.tag(
        {"title": "A" * 130}, np.asarray([1, 0, 0, 0]), np.asarray([1, 0, 0, 0])
    )
    assert isinstance(result, ItemTags)
    assert result.slot == "top"
    assert result.formality == 1
    assert result.niche == 0
    assert result.fit == "slim"
    assert result.era == "90s"
    assert result.logo_heavy is True
    assert result.materials == ["cotton", "polyester"]
    assert result.aesthetics == ["streetwear", "vintage", "minimal"]
    assert len(result.one_line) == 120


@pytest.mark.parametrize(
    ("hint", "expected"),
    [
        ("m", "menswear"), ("men", "menswear"), ("menswear", "menswear"),
        ("w", "womenswear"), ("women", "womenswear"),
        ("womenswear", "womenswear"), ("u", "unisex"), ("unisex", "unisex"),
    ],
)
def test_gender_hint_variants_override_vote(hint, expected):
    result = _tagger([([1, 0, 0, 0], _tags(gender="menswear"))]).tag(
        {"gender_hint": hint}, None, np.asarray([1, 0, 0, 0])
    )
    assert result.gender == expected


def test_text_gender_overrides_gender_hint():
    result = _tagger([([1, 0, 0, 0], _tags(gender="menswear"))]).tag(
        {"title": "Women's Ava Shirt", "gender_hint": "m"},
        None,
        np.asarray([1, 0, 0, 0]),
    )
    assert result.gender == "womenswear"


@pytest.mark.parametrize(
    "item_fields",
    [
        {"product_type": "Men's T-Shirts"},
        {"raw_json": json.dumps({"record": {"product_type": "Men's T-Shirts"}})},
    ],
)
def test_product_type_overrides_gender_hint(item_fields):
    result = _tagger([([1, 0, 0, 0], _tags(gender="womenswear"))]).tag(
        {"title": "Essential Tee", "gender_hint": "w", **item_fields},
        None,
        np.asarray([1, 0, 0, 0]),
    )
    assert result.gender == "menswear"


def test_gender_without_hint_uses_weighted_vote():
    result = _tagger([
        ([1, 0, 0, 0], _tags(gender="womenswear")),
        ([0.8, 0.6, 0, 0], _tags(gender="menswear")),
    ], k=2).tag({}, None, np.asarray([1, 0, 0, 0]))
    assert result.gender == "womenswear"


def test_all_nonpositive_similarities_use_uniform_weights():
    result = _tagger([
        ([-1, 0, 0, 0], _tags(slot="top")),
        ([-1, 0, 0, 0], _tags(slot="bottom")),
    ], k=2).tag({}, None, np.asarray([1, 0, 0, 0]))
    assert result.slot == "top"


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        ({"condition_hint": "second-hand"}, "second-hand"),
        ({"condition_hint": "new"}, "new"),
        ({"source": "vinted"}, "second-hand"),
        ({"source": "store.example"}, "new"),
    ],
)
def test_condition_rules(item, expected):
    assert _tagger([([1, 0, 0, 0], _tags())]).tag(
        item, None, np.asarray([1, 0, 0, 0])
    ).condition == expected


def test_niche_rounds_to_two_decimals():
    result = _tagger([
        ([1, 0, 0, 0], _tags(niche=0.123)),
        ([0.8, 0.6, 0, 0], _tags(niche=0.777)),
    ], k=2).tag({}, None, np.asarray([1, 0, 0, 0]))
    assert result.niche == round((0.123 + 0.777 * 0.8) / 1.8, 2)


def test_formality_rounds_weighted_mean():
    result = _tagger([
        ([1, 0, 0, 0], _tags(formality=3)),
        ([1, 0, 0, 0], _tags(formality=4)),
    ], k=2).tag({}, None, np.asarray([1, 0, 0, 0]))
    assert result.formality == 4


def test_category_comes_from_best_neighbour_with_chosen_slot():
    result = _tagger([
        ([1, 0, 0, 0], _tags(slot="dress", category="dress")),
        ([0.8, 0.6, 0, 0], _tags(slot="top", category="shirt")),
        ([0.8, 0.6, 0, 0], _tags(slot="top", category="tee")),
    ], k=3).tag({}, None, np.asarray([1, 0, 0, 0]))
    assert result.slot == "top"
    assert result.category == "shirt"


def test_image_colour_argmax_and_no_image_knn_colour(monkeypatch):
    prompts = np.zeros((len(local_tagger.COLOURS), 4), dtype=np.float32)
    prompts[local_tagger.COLOURS.index("navy"), 1] = 1
    monkeypatch.setattr(local_tagger, "_colour_prompt_vectors", lambda: prompts)
    tagger = _tagger([
        ([1, 0, 0, 0], _tags(colours=["black"])),
        ([0.8, 0.6, 0, 0], _tags(colours=["pink"])),
    ], k=2)
    assert tagger.tag({}, np.asarray([0, 1, 0, 0]), np.asarray([1, 0, 0, 0])).colours == ["navy"]
    assert tagger.tag({}, None, np.asarray([1, 0, 0, 0])).colours == ["black"]


def test_from_db_excludes_local_reference_rows(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "local-tagger.sqlite")
    db._INITIALIZED_DATABASES.clear()
    db.init_db()
    vector = np.asarray([1, 0, 0, 0], dtype=np.float32)
    with db.db_connection() as connection:
        for item_id, request_id in (("gpt", "req-1"), ("local", "local:clip-knn-v1")):
            connection.execute(
                """INSERT INTO items
                   (id, source, source_url, title, image_url, fetched_at, raw_json,
                    tags_json, tags_request_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    item_id, "source", f"https://example.test/{item_id}", item_id,
                    "https://example.test/image.jpg", "now", "{}",
                    json.dumps(_tags()), request_id,
                ),
            )
    for item_id in ("gpt", "local"):
        db.store_item_embedding(item_id, "clip32", vector, vector, vector)
    result = LocalTagger.from_db()
    assert len(result.reference) == 1
    db._INITIALIZED_DATABASES.clear()
