from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks

from app import phase2_api, styles
from app.mubit import TaskError
from app.outfits import stylist_user_content
from app.styles import (
    DecoderOut,
    decode_brief,
    decoder_user_content,
    load_glossary,
    match_text,
    style_notes,
)
from app.taste import taste_user_content


def test_glossary_is_complete_and_aliases_are_unique():
    glossary = load_glossary()
    owners = {}

    assert len(glossary) == 56
    for entry in glossary:
        assert entry["queries"]
        for alias in entry["aliases"]:
            normalized = alias.casefold().strip()
            assert normalized not in owners or owners[normalized] == entry["name"]
            owners[normalized] = entry["name"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("roadman nike tech fleece", ["roadman"]),
        ("Roadmen fits", ["roadman"]),
        ("road trip to the alps", []),
        ("casual friday", []),
        ("gorpcore and techwear", ["gorpcore", "techwear"]),
        ("roadman gorpcore techwear uk drill", ["roadman", "gorpcore", "techwear"]),
    ],
)
def test_match_text_uses_whole_aliases_in_first_occurrence_order(text, expected):
    assert match_text(text) == expected


def test_decoder_user_content_lists_glossary_then_brief():
    content = decoder_user_content("Roadman")

    assert content.startswith("Glossary:\nroadman: roadman, roadmen")
    assert content.endswith("\n\nBrief:\nRoadman")


def test_decode_brief_canonicalizes_only_glossary_names(monkeypatch):
    def fake_call_task(task, system, model, content, output_model):
        assert task == "decoder"
        assert content.endswith("\n\nBrief:\nroadman")
        return (
            output_model(
                styles=["Roadman", "roadman", "made up"],
                unknown_terms=["made up"],
                confidence=0.9,
            ),
            SimpleNamespace(request_id="decoder-1"),
        )

    monkeypatch.setattr(styles, "call_task", fake_call_task)

    assert decode_brief({"text": "roadman"}) == ["roadman"]


def test_decode_brief_falls_back_to_regex_on_task_error(monkeypatch):
    def fail_call_task(*_args):
        raise TaskError("decoder unavailable")

    monkeypatch.setattr(styles, "call_task", fail_call_task)

    assert decode_brief({"text": "Roadmen fits"}) == ["roadman"]


def test_decode_brief_uses_regex_for_empty_result_and_caps_styles(monkeypatch):
    monkeypatch.setattr(
        styles,
        "call_task",
        lambda *_args: (
            DecoderOut(styles=[], unknown_terms=[], confidence=0.0),
            None,
        ),
    )
    assert decode_brief({"text": "gorpcore and techwear"}) == [
        "gorpcore",
        "techwear",
    ]

    monkeypatch.setattr(
        styles,
        "call_task",
        lambda *_args: (
            DecoderOut(
                styles=["roadman", "gorpcore", "techwear", "uk drill"],
                unknown_terms=[],
                confidence=1.0,
            ),
            None,
        ),
    )
    assert decode_brief({"text": "all four"}) == [
        "roadman",
        "gorpcore",
        "techwear",
    ]


def test_taste_user_content_adds_notes_without_serializing_style_refs():
    brief = {
        "text": "roadman",
        "style_refs": ["roadman"],
        "gender": "menswear",
    }
    content = taste_user_content(brief, None, 1, [])

    assert "Style notes (what these style names usually mean):" in content
    assert "Nike Tech Fleece" in content
    brief_section = content.split("\n\n", maxsplit=1)[0]
    assert "style_refs" not in brief_section
    assert "Style notes" not in taste_user_content(
        {"text": "A plain city brief"}, None, 1, []
    )


def test_stylist_user_content_adds_notes_only_when_present():
    base = stylist_user_content({}, 1, [], [])
    assert stylist_user_content({}, 1, [], [], style_notes=None) == base
    assert stylist_user_content({}, 1, [], [], style_notes=[]) == base
    assert "Style notes: roadman: pieces matching tracksuit" in stylist_user_content(
        {},
        1,
        [],
        [],
        style_notes=style_notes({"style_refs": ["roadman"]}),
    )


def test_start_fetch_prioritizes_deduplicated_style_queries(monkeypatch):
    monkeypatch.setattr(phase2_api.db, "save_fetch_job", lambda *_args: None)
    tasks = BackgroundTasks()
    profile = {
        "id": "board-1",
        "brief": {"gender": "menswear", "style_refs": ["roadman"]},
    }

    phase2_api._start_fetch(
        profile,
        ["already searched", "nike tech fleece tracksuit"],
        tasks,
    )

    assert tasks.tasks[0].args[2] == [
        "nike tech fleece tracksuit",
        "trapstar hoodie",
        "already searched",
    ]
