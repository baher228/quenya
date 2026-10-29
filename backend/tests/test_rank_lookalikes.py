import numpy as np
import pytest

from app.rank import drop_lookalikes, image_key, product_key


def _item(item_id, title, *, shop="shop.example", image=None):
    return {
        "id": item_id,
        "source": "fallback-shop",
        "source_url": f"https://www.{shop}/items/{item_id}",
        "title": title,
        "image_url": image or f"https://images.example/{item_id}.jpg",
    }


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Dickies, Cargo Pants! - NAVY", "shop.example|dickies cargo pants"),
        ("Single - Navy", "shop.example|single navy"),
    ],
)
def test_product_key_normalizes_shop_title_and_colour_suffix(title, expected):
    assert product_key(_item("item", title)) == expected


def test_product_key_falls_back_to_source():
    assert product_key({"source": "VINTED", "title": "Red Jacket!"}) == (
        "vinted|red jacket"
    )


def test_image_key_strips_query_and_returns_none_for_missing_url():
    assert image_key(
        {"image_url": "https://images.example/product.jpg?width=640&token=abc"}
    ) == "https://images.example/product.jpg"
    assert image_key({"image_url": "  "}) is None
    assert image_key({}) is None


def test_drop_lookalikes_removes_exact_keys_and_keeps_first_duplicates():
    liked = _item("liked", "Levi's 501 Jeans - Navy")
    disliked = _item("disliked", "Quiet Leather Jacket - Black")
    ranked = [
        _item("same-liked-product", "Levi's 501 Jeans - Green"),
        _item("same-disliked-product", "Quiet Leather Jacket - Brown"),
        _item(
            "same-image",
            "Another Product",
            shop="other.example",
            image="https://images.example/liked.jpg?size=small",
        ),
        _item("first-duplicate", "Adidas Track Jacket - Red"),
        _item("second-duplicate", "Adidas Track Jacket - Blue"),
        _item(
            "first-image-duplicate",
            "First Different Product",
            image="https://images.example/duplicate.jpg?size=small",
        ),
        _item(
            "second-image-duplicate",
            "Second Different Product",
            image="https://images.example/duplicate.jpg?size=large",
        ),
    ]
    swipes = [
        (liked, True),
        (disliked, False),
        (
            _item(
                "image-swipe",
                "Different Product",
                image="https://images.example/liked.jpg?size=large",
            ),
            True,
        ),
    ]

    result = drop_lookalikes(ranked, swipes, {})

    assert [item["id"] for item in result] == [
        "first-duplicate",
        "first-image-duplicate",
    ]


def _cosine_vector(cosine, magnitude=1.0):
    return np.asarray(
        [cosine * magnitude, np.sqrt(1 - cosine**2) * magnitude],
        dtype=np.float32,
    )


@pytest.mark.parametrize(
    ("liked", "cosine", "expected"),
    [
        (False, 0.93, []),
        (True, 0.93, ["candidate"]),
        (True, 0.97, []),
        (False, 0.5, ["candidate"]),
    ],
)
def test_drop_lookalikes_uses_separate_normalized_cosine_thresholds(
    liked, cosine, expected
):
    swiped = _item("swiped", "Reference Item")
    candidate = _item("candidate", "Similar Item", shop="another.example")

    result = drop_lookalikes(
        [candidate],
        [(swiped, liked)],
        {
            "swiped": np.asarray([5, 0], dtype=np.float32),
            "candidate": _cosine_vector(cosine, magnitude=3.0),
        },
    )

    assert [item["id"] for item in result] == expected


def test_drop_lookalikes_keeps_different_products_with_the_same_colour():
    swiped = _item("swiped", "Adidas Track Jacket - Navy")
    different_product = _item("different", "Dickies Cargo Trousers - Navy")

    result = drop_lookalikes([different_product], [(swiped, False)], {})

    assert [item["id"] for item in result] == ["different"]


def test_drop_lookalikes_does_not_filter_candidates_without_vectors():
    swiped = _item("swiped", "Reference Item")
    candidate = _item("candidate", "Similar Item", shop="another.example")

    result = drop_lookalikes(
        [candidate],
        [(swiped, False)],
        {"swiped": np.asarray([1, 0], dtype=np.float32)},
    )

    assert [item["id"] for item in result] == ["candidate"]


def test_drop_lookalikes_limits_vector_filtering_to_the_requested_window():
    swiped = _item("swiped", "Reference Item")
    first = _item("first", "First Similar Item", shop="first.example")
    second = _item("second", "Second Similar Item", shop="second.example")

    result = drop_lookalikes(
        [first, second],
        [(swiped, False)],
        {
            "swiped": np.asarray([1, 0], dtype=np.float32),
            "first": np.asarray([1, 0], dtype=np.float32),
            "second": np.asarray([1, 0], dtype=np.float32),
        },
        vector_window=1,
    )

    assert [item["id"] for item in result] == ["second"]
