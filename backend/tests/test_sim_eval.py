import numpy as np
import pytest

from app import sim_eval


@pytest.fixture(autouse=True)
def fresh_postgres_schema():
    yield


@pytest.mark.xfail(strict=False, reason="needs PR #90")
def test_prod_replica_matches_production_swipe_sequences_and_metrics(monkeypatch):
    monkeypatch.setattr(sim_eval, "REQUESTS", 3)
    world = sim_eval.make_world(0, 400)
    sequences = []
    original_metrics = sim_eval.metrics

    def capture_metrics(world, user, swipes):
        sequences.append(list(swipes))
        return original_metrics(world, user, swipes)

    monkeypatch.setattr(sim_eval, "metrics", capture_metrics)
    replica = sim_eval.embed_policy()

    for user_seed in range(2):
        user = sim_eval.make_user(world, user_seed)
        prod_metrics = sim_eval.simulate(world, user, sim_eval.policy_prod)
        replica_metrics = sim_eval.simulate(world, user, replica)
        assert sequences[-2] == sequences[-1]
        assert prod_metrics == replica_metrics


def test_selector_avoids_within_deck_lookalikes_when_alternatives_exist(monkeypatch):
    monkeypatch.setattr(sim_eval, "REQUESTS", 3)
    world = sim_eval.make_world(0, 400)
    user = sim_eval.make_user(world, 0)
    swipes = []
    alternative_opportunities = 0
    original_selector = sim_eval._selector
    original_metrics = sim_eval.metrics

    def capture_metrics(world, user, observed_swipes):
        swipes.extend(observed_swipes)
        return original_metrics(world, user, observed_swipes)

    def observe_selector(context, raw_fn, *, diversity, rng):
        select = original_selector(context, raw_fn, diversity=diversity, rng=rng)

        def checked_select(pool, selected):
            nonlocal alternative_opportunities
            chosen = select(pool, selected)
            if selected and pool:
                pool_vectors = np.asarray(
                    [context.vector(item["id"]) for item in pool]
                )
                selected_vectors = np.asarray(
                    [context.vector(item["id"]) for item in selected]
                )
                similarities = pool_vectors @ selected_vectors.T
                allowed = np.max(similarities, axis=1) < sim_eval.LIKE_COS
                if allowed.any() and (~allowed).any():
                    alternative_opportunities += 1
                    assert np.all(
                        context.vector(chosen["id"]) @ selected_vectors.T
                        < sim_eval.LIKE_COS
                    )
            return chosen

        return checked_select

    monkeypatch.setattr(sim_eval, "_selector", observe_selector)
    monkeypatch.setattr(sim_eval, "metrics", capture_metrics)
    sim_eval.simulate(world, user, sim_eval.embed_policy())

    assert alternative_opportunities > 0
    for start in range(0, len(swipes), sim_eval.DECK):
        deck_ids = [
            swipe["item_id"] for swipe in swipes[start : start + sim_eval.DECK]
        ]
        vectors = np.asarray(
            [world.vectors[world.index[item_id]] for item_id in deck_ids]
        )
        similarities = vectors @ vectors.T
        assert not np.any(similarities[np.triu_indices(len(deck_ids), k=1)] >= sim_eval.LIKE_COS)


def test_metrics_counts_cosine_relist_but_not_unrelated_card():
    world = sim_eval.make_world(0, 3)
    vectors = np.zeros((3, sim_eval.DIM), dtype=np.float32)
    vectors[0, 0] = 1.0
    vectors[1, :2] = [0.97, np.sqrt(1 - 0.97**2)]
    vectors[2, 2] = 1.0
    world.vectors = vectors
    user = sim_eval.make_user(world, 0)
    swipes = [
        {"item_id": "i0", "liked": True},
        {"item_id": "i1", "liked": False},
        {"item_id": "i2", "liked": True},
    ]

    result = sim_eval.metrics(world, user, swipes)

    assert result["lookalike shown"] == pytest.approx(1 / 3)
    assert result["repeat shown"] == 0


def test_run_summary_and_render_include_paired_random_comparison():
    report = sim_eval.run(
        users=3,
        n_items=400,
        workers=1,
        names=["embed (prod)", "random"],
    )
    summary = report["summary"]

    assert summary["embed (prod)"]
    assert summary["random"]
    assert all(
        "diff" not in entry and "diff_ci" not in entry
        for entry in summary["embed (prod)"].values()
    )
    assert all(
        "diff" in entry and "diff_ci" in entry
        for entry in summary["random"].values()
    )
    assert "| embed (prod) |" in sim_eval.render(report)
