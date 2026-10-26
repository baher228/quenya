import numpy as np

from app import embedding, local_tagger, tagging
from app.local_tagger import LocalTagger
from app.schemas import ItemTags


def _tags():
    return {
        "slot": "top",
        "category": "tee",
        "colours": ["black"],
        "materials": ["cotton"],
        "fit": "regular",
        "era": "contemporary",
        "aesthetics": ["streetwear"],
        "formality": 3,
        "niche": 0.4,
        "gender": "unisex",
        "logo_heavy": False,
        "condition": "new",
        "one_line": "A cotton tee.",
    }


def test_tag_item_uses_local_tagger_and_returns_compatibly(monkeypatch):
    monkeypatch.setattr(tagging, "QUENYA_TAGGER", "local")
    monkeypatch.setattr(tagging, "_image_input", lambda _item: "fixture.jpg")
    vector = np.asarray([1, 0, 0, 0], dtype=np.float32)
    monkeypatch.setattr(
        embedding, "encode_images", lambda images: np.asarray([vector] * len(images))
    )
    monkeypatch.setattr(
        embedding, "encode_texts", lambda texts: np.asarray([vector] * len(texts))
    )
    colour_prompts = np.zeros((len(local_tagger.COLOURS), 4), dtype=np.float32)
    colour_prompts[0, 0] = 1
    monkeypatch.setattr(
        local_tagger, "_colour_prompt_vectors", lambda: colour_prompts
    )
    instance = LocalTagger([(vector, _tags())])
    monkeypatch.setattr(tagging, "_local_tagger", lambda: instance)
    item = {
        "id": "item-1",
        "title": "Cotton tee",
        "source": "shop.example",
        "price_gbp": 20,
        "image_path": "fixture.jpg",
    }

    tags, meta = tagging.tag_item(item, note="make it a dress")
    vector_tags, vector_meta, vectors = tagging.tag_item_with_vectors(item)

    assert isinstance(tags, ItemTags)
    assert tags.slot == "top"
    assert meta.request_id == local_tagger.LOCAL_TAGGER_ID
    assert isinstance(vector_tags, ItemTags)
    assert vector_meta.request_id == local_tagger.LOCAL_TAGGER_ID
    assert vectors is not None
    np.testing.assert_array_equal(vectors[0], vector)
    np.testing.assert_array_equal(vectors[1], vector)
    np.testing.assert_allclose(vectors[2], vector)
