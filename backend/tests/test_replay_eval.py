import gzip
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from app.replay_eval import (
    Dataset,
    State,
    Style,
    Swipe,
    auc,
    blend_policy,
    bootstrap_p,
    cluster_bootstrap,
    embed_policy,
    logged_stats,
    render,
    replay,
    run,
    state_at,
    tags_policy,
    taste_at,
)


@pytest.fixture(autouse=True)
def fresh_postgres_schema():
    yield


def _item(item_id, *, title=None, source_url=None, tags=None):
    return {
        "id": item_id,
        "source": "shop.example",
        "source_url": source_url or f"https://shop.example/items/{item_id}",
        "title": title or item_id,
        "image_url": f"https://shop.example/images/{item_id}.jpg",
        "availability": "InStock",
        "price_gbp": 20,
        "tags": tags or {"niche": 0},
    }


def _style(style_id, owner, item_ids, labels=None):
    labels = labels or [index % 2 == 0 for index in range(len(item_ids))]
    return Style(
        id=style_id,
        owner=owner,
        role="tester",
        kind="user",
        created_at="2025-01-01T00:00:00Z",
        arm="embed",
        brief={},
        swipes=[
            Swipe(
                item_id=item_id,
                liked=liked,
                created_at=f"2025-01-01T00:{index:02d}:00Z",
                seq=index,
            )
            for index, (item_id, liked) in enumerate(zip(item_ids, labels, strict=True))
        ],
    )


def _write_export(path, records):
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
    return path


def test_auc_perfect_reversed_tied_and_single_class():
    labels = np.asarray([1, 1, 0, 0])
    assert auc(np.asarray([4, 3, 2, 1]), labels) == 1.0
    assert auc(np.asarray([1, 2, 3, 4]), labels) == 0.0
    assert auc(np.asarray([1, 1, 1, 1]), labels) == 0.5
    assert auc(np.asarray([1, 2, 3]), np.asarray([1, 1, 1])) is None


def test_taste_and_state_are_strictly_before_cutoff_and_style_local():
    style = _style("style-a", "owner-a", ["a", "b", "c"])
    style.tastes = [
        ("2025-01-01T00:00:00Z", 1, {"version": 1}),
        ("2025-01-01T00:01:00Z", 2, {"version": 2}),
        ("2025-01-01T00:01:00Z", 3, {"version": 3}),
        ("2025-01-01T00:02:00Z", 4, {"version": 4}),
    ]

    state = state_at(style, 1)
    assert state.cutoff_at == style.swipes[1].created_at
    assert [swipe.item_id for swipe in state.history] == ["a"]
    assert state.taste == {"version": 1}
    assert taste_at(style, style.swipes[1].created_at) == {"version": 1}
    assert taste_at(style, None) == {"version": 4}
    later_state = state_at(style, 2)
    assert [swipe.item_id for swipe in later_state.history] == ["a", "b"]
    assert later_state.taste == {"version": 3}


def test_replay_policies_never_receive_future_or_scored_swipes():
    style = _style("style-a", "owner-a", [f"item-{index}" for index in range(12)])
    items = {swipe.item_id: _item(swipe.item_id) for swipe in style.swipes}
    data = Dataset(styles={style.id: style}, items=items, vectors={}, sample=np.zeros((0, 3)))
    calls = []

    def guarded_policy(state, candidates, _data):
        candidate_ids = {item["id"] for item in candidates}
        assert all(
            state.cutoff_at is None or swipe.created_at < state.cutoff_at
            for swipe in state.history
        )
        assert {swipe.item_id for swipe in state.history}.isdisjoint(candidate_ids)
        calls.append((state.cutoff_at, candidate_ids))
        return np.zeros(len(candidates))

    rows = replay(
        data, {"guarded": guarded_policy}, window=6, step=2, min_heldout=3
    )
    assert calls
    assert len(rows) == len(calls)


def test_replay_rows_for_one_style_do_not_depend_on_another_owner():
    a_ids = [f"a-{index}" for index in range(10)]
    b_ids = [f"b-{index}" for index in range(10)]
    style_a = _style("style-a", "owner-a", a_ids)
    style_a.tastes = [("2024-12-31T00:00:00Z", 1, {"likes": {"blue": 0.5}})]
    style_b = _style("style-b", "owner-b", b_ids)
    style_b.tastes = [("2024-12-31T00:00:00Z", 1, {"likes": {"red": 0.5}})]
    items = {
        item_id: _item(item_id, tags={"aesthetics": [color], "niche": 0})
        for item_id, color in (
            [(item_id, "blue") for item_id in a_ids]
            + [(item_id, "red") for item_id in b_ids]
        )
    }
    sample = np.zeros((0, 3), dtype=np.float32)
    a_only = Dataset({style_a.id: style_a}, items, {}, sample)
    both = Dataset({style_a.id: style_a, style_b.id: style_b}, items, {}, sample)
    policies = {"tags": tags_policy}

    baseline = [
        row for row in replay(a_only, policies, window=6, step=2, min_heldout=4)
        if row["style"] == style_a.id
    ]
    style_b.swipes = [
        Swipe(
            item_id,
            index % 3 == 0,
            f"2025-02-01T00:{index:02d}:00Z",
            index,
        )
        for index, item_id in enumerate(reversed(b_ids))
    ]
    style_b.tastes = [("2025-02-01T00:00:00Z", 8, {"likes": {"green": 20}})]
    changed = [
        row for row in replay(both, policies, window=6, step=2, min_heldout=4)
        if row["style"] == style_a.id
    ]
    assert changed == baseline


def test_default_blend_scores_match_embed_policy_with_missing_vectors():
    brief = np.asarray([0.4, -0.3, 0.8], dtype=np.float32)
    style = Style(
        id="style-a",
        owner="owner-a",
        role="tester",
        kind="user",
        created_at="",
        arm="embed",
        brief={},
        brief_vector=brief,
    )
    items = [_item("candidate-a"), _item("candidate-missing"), _item("candidate-b")]
    data = Dataset(
        styles={style.id: style},
        items={item["id"]: item for item in items},
        vectors={
            "candidate-a": np.asarray([1, 0, 0], dtype=np.float32),
            "candidate-b": np.asarray([0, 1, 0], dtype=np.float32),
            "history-like": np.asarray([0, 0, 1], dtype=np.float32),
        },
        sample=np.asarray(
            [[1, 0, 0], [0, 1, 0], [0, 0, 1], [-1, 0, 0]], dtype=np.float32
        ),
    )
    default_blend = blend_policy()
    states = (
        State(style, (), {"likes": {"casual": 0.5}}, None),
        State(
            style,
            (Swipe("history-like", True, "2025-01-01T00:00:00Z", 0),
             Swipe("history-missing", False, "2025-01-01T00:01:00Z", 1)),
            {"likes": {"casual": 0.5}},
            "2025-01-01T00:02:00Z",
        ),
    )
    for state in states:
        np.testing.assert_allclose(
            default_blend(state, items, data),
            embed_policy(state, items, data),
            atol=1e-6,
            rtol=1e-6,
        )


def test_cluster_bootstrap_constant_values_and_positive_difference_p_value():
    constant = [("owner-a", 3.25), ("owner-a", 3.25), ("owner-b", 3.25)]
    assert cluster_bootstrap(constant, reps=100) == (3.25, 3.25, 3.25)
    positive = [("owner-a", 0.4), ("owner-b", 0.5), ("owner-c", 0.6)]
    assert bootstrap_p(positive, reps=500) < 0.05


def test_embedding_replay_beats_tags_for_hidden_direction():
    rng = np.random.default_rng(1729)

    def unit_rows(values):
        return values / np.linalg.norm(values, axis=1, keepdims=True)

    hidden = rng.normal(size=512).astype(np.float32)
    hidden /= np.linalg.norm(hidden)
    vectors = unit_rows(rng.normal(size=(340, 512)).astype(np.float32))
    catalogue = unit_rows(rng.normal(size=(300, 512)).astype(np.float32))
    ids = [f"signal-{index}" for index in range(40)]
    labels = [bool(vector @ hidden > 0) for vector in vectors[:40]]
    style = _style("style-signal", "owner-signal", ids, labels)
    style.brief_vector = hidden
    items = {
        item_id: _item(item_id, tags={"niche": 0})
        for item_id in ids
    }
    data = Dataset(
        styles={style.id: style},
        items=items,
        vectors={
            item_id: vector
            for item_id, vector in zip(ids, vectors[:len(ids)], strict=True)
        },
        sample=catalogue,
    )

    rows = replay(
        data,
        {"embed (prod)": embed_policy, "tags (prod)": tags_policy},
        window=20,
        step=20,
        min_heldout=20,
    )
    warm = {row["policy"]: row["auc"] for row in rows if row["cutoff"] == 20}
    assert warm["embed (prod)"] > 0.7
    assert warm["embed (prod)"] > warm["tags (prod)"]


def test_logged_stats_counts_same_shop_title_as_a_repeat():
    style = _style("style-a", "owner-a", ["already-swiped"], [True])
    style.swipes[0] = Swipe(
        "already-swiped", True, "2025-01-01T00:00:00Z", 0
    )
    repeated = {
        "id": "same-product-other-variant",
        "item_id": "same-product-other-variant",
        "arm": "embed",
        "position": 1,
        "created_at": "2025-01-01T00:01:00Z",
    }
    unrelated = {
        "id": "new-product",
        "item_id": "new-product",
        "arm": "embed",
        "position": 2,
        "created_at": "2025-01-01T00:01:00Z",
    }
    style.deck_events = [repeated, unrelated]
    items = {
        "already-swiped": _item(
            "already-swiped", title="Canvas Jacket - Blue",
            source_url="https://www.shop.example/products/old",
        ),
        "same-product-other-variant": _item(
            "same-product-other-variant", title="Canvas Jacket - Red",
            source_url="https://shop.example/products/new",
        ),
        "new-product": _item(
            "new-product", title="Linen Trousers",
            source_url="https://shop.example/products/trousers",
        ),
    }
    data = Dataset({style.id: style}, items, {}, np.zeros((0, 3), dtype=np.float32))

    stats = logged_stats(data)
    assert stats["exposures"] == 2
    assert stats["repeat_rate"] == (0.5, 0.5, 0.5)


def test_run_render_and_module_cli_on_synthetic_export(tmp_path):
    ids = [f"item-{index}" for index in range(6)]
    records = [
        {"kind": "meta", "version": 1, "vectors": "sample"},
        {
            "kind": "style",
            "style": "style-hash",
            "owner": "owner-hash",
            "role": "tester",
            "profile_kind": "user",
            "created_at": "2025-01-01T00:00:00Z",
            "arm": "embed",
            "brief": {},
        },
        *[
            {"kind": "item", **_item(item_id)}
            for item_id in ids
        ],
        *[
            {
                "kind": "swipe",
                "style": "style-hash",
                "item_id": item_id,
                "liked": index % 2 == 0,
                "created_at": f"2025-01-01T00:{index:02d}:00Z",
                "seq": index,
            }
            for index, item_id in enumerate(ids)
        ],
        {
            "kind": "deck_event",
            "style": "style-hash",
            "item_id": ids[0],
            "arm": "embed",
            "policy": "prod",
            "position": 1,
            "score": 0.4,
            "created_at": "2025-01-01T01:00:00Z",
        },
    ]
    path = _write_export(tmp_path / "synthetic.jsonl.gz", records)

    report = run(path)
    text = render(report)
    assert "## Replay" in text
    assert "## Logged decks" in text

    result = subprocess.run(
        [sys.executable, "-m", "app.replay_eval", str(path)],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert "## Replay" in result.stdout
    assert "## Logged decks" in result.stdout
