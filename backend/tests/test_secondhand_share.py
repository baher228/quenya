import pytest

from app.rank import make_deck, secondhand_share


@pytest.mark.parametrize(
    ("brief", "profile", "expected"),
    [
        ({"condition": "second-hand"}, {}, 1.0),
        ({"condition": "new"}, {}, 0.0),
        ({"budget_gbp": 100, "looking_for": "outfits"}, {"niche_level": 0.9}, 0.85),
        ({"budget_gbp": 800, "looking_for": "outfits"}, {"niche_level": 0.2}, 0.17),
        ({"budget_gbp": 300, "looking_for": "outfits"}, {"niche_level": 0.5}, 0.5),
    ],
)
def test_secondhand_share(brief, profile, expected):
    assert secondhand_share(brief, profile) == expected


def _item(index: int, secondhand: bool) -> dict:
    return {
        "id": str(index),
        "source": "vinted" if secondhand else "ssense.com",
        "price_gbp": 30,
        "tags": {
            "slot": "top",
            "condition": "second-hand" if secondhand else "new",
            "aesthetics": ["classic"],
        },
    }


def test_deck_approximates_requested_secondhand_share():
    ranked = [_item(i, True) for i in range(15)] + [_item(i + 15, False) for i in range(15)]
    deck = make_deck(ranked, 10, {"pieces": ["top"]}, secondhand_share=0.3)
    count = sum(item["tags"]["condition"] == "second-hand" for item in deck)
    assert len(deck) == 10
    assert abs(count - 3) <= 1


def test_deck_fills_when_only_secondhand_items_are_available():
    deck = make_deck(
        [_item(i, True) for i in range(15)],
        10,
        {"pieces": ["top"]},
        secondhand_share=0.3,
    )
    assert len(deck) == 10
