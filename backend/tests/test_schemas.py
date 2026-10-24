import pytest
from pydantic import ValidationError

from app.schemas import InspirationRead, ItemTags, StylistOutput, TasteProfile


def test_item_tags_accepts_valid_payload_and_rejects_invalid_values():
    payload = {
        "slot": "outerwear",
        "category": "chore jacket",
        "colours": ["olive"],
        "materials": ["cotton"],
        "fit": "relaxed",
        "era": "contemporary",
        "aesthetics": ["japanese workwear"],
        "formality": 2,
        "niche": 0.6,
        "gender": "unisex",
        "logo_heavy": False,
        "condition": "new",
        "one_line": "A relaxed layer with a practical shape.",
    }
    assert ItemTags.model_validate(payload).slot == "outerwear"
    with pytest.raises(ValidationError):
        ItemTags.model_validate({**payload, "formality": 6})
    with pytest.raises(ValidationError):
        ItemTags.model_validate({**payload, "extra": True})


def test_other_task_schemas_validate():
    inspiration = {
        "garments": [{"slot": "top", "category": "shirt", "colours": ["white"]}],
        "aesthetics": ["ivy"],
        "palette": ["white", "navy"],
        "fit": "regular",
        "era": "contemporary",
        "search_queries": ["ivy oxford shirt"],
        "one_line": "A clean layered look.",
    }
    profile = {
        "summary": "You prefer relaxed workwear. You avoid loud logos.",
        "likes": {"japanese workwear": 0.8},
        "avoids": {"large logos": 0.9},
        "rules": ["no large logos"],
        "palette": ["olive", "navy", "cream"],
        "niche_level": 0.7,
        "price_ceiling_gbp": 250,
        "condition_preference": "both",
        "search_queries": ["japanese workwear jacket"],
        "changes": [],
    }
    stylist = {
        "outfits": [
            {
                "title": "Canvas and denim",
                "mood": "Japanese workwear, relaxed",
                "piece_ids": ["vinted:123"],
                "why": "You kept relaxed workwear pieces.",
            }
        ]
    }
    assert InspirationRead.model_validate(inspiration)
    assert TasteProfile.model_validate(profile)
    assert StylistOutput.model_validate(stylist)
    with pytest.raises(ValidationError):
        TasteProfile.model_validate({**profile, "unexpected": "field"})
