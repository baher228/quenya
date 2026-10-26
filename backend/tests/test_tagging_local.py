import numpy as np

from app import embedding, local_tagger, tag, tagging
from app.llm import CallMeta
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


def test_llm_text_gender_override_is_saved(monkeypatch):
    monkeypatch.setattr(tagging, "QUENYA_TAGGER", "llm")
    monkeypatch.setattr(tagging, "user_content", lambda _item, _note=None: [])
    meta = CallMeta("request-1", "test-model", 12, None, None)
    monkeypatch.setattr(
        tagging,
        "call_task",
        lambda *_args, **_kwargs: ({**_tags(), "gender": "menswear"}, meta),
    )
    saved = []
    monkeypatch.setattr(
        tag.db,
        "save_tags",
        lambda item_id, tags, request_id, cached_image_path=None: saved.append(
            (item_id, tags, request_id, cached_image_path)
        ),
    )

    status, latency, error = tag._tag_one({
        "id": "item-women",
        "title": "Women's Ava Shirt",
        "source_url": "https://shop.example/products/womens-ava-shirt",
        "image_path": "image.jpg",
    })

    assert (status, latency, error) == ("ok", 12, None)
    assert saved[0][1]["gender"] == "womenswear"
