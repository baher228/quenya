"""Simulated-user benchmark for the deck ranker (needs no production data).

A synthetic catalogue lives in a CLIP-like space (shared offset + style + colour
directions + noise, with relisted near-duplicates). Each simulated user has a
hidden taste (1-3 styles, liked/hated colours, per-item idiosyncrasy) and writes
a partial brief. Policies build 10-card decks with the production helpers; the
user swipes every card with common random numbers, so policies are compared
paired on identical users and identical swipe outcomes.

This is a sanity benchmark, not evidence about real users: confirm any gain with
``app.replay_eval`` on real swipes or an A/B arm before trusting it.

    python -m app.sim_eval --users 120 --json sim.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from typing import Any

import numpy as np

from app import embedding_ranker
from app.embedding_ranker import (
    PRODUCTION_WEIGHT,
    choose_bootdiv,
    history_query,
    rank_candidates,
    zstats,
)
from app.rank import add_exploration, drop_lookalikes, make_deck, rank_items, score_item

DIM = 64
N_STYLES = 24
COLOURS = ("black", "white", "navy", "grey", "beige", "green", "red", "brown")
KINDS = (
    ("top", "t-shirt", 0.25), ("top", "knit jumper", 0.15), ("bottom", "trousers", 0.3),
    ("outerwear", "jacket", 0.15), ("shoes", "trainers", 0.15),
)
DECK = 10
REQUESTS = 10
SHARE = 0.3
LIKE_COS = 0.95
DISLIKE_COS = 0.90


def _unit(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=-1, keepdims=True)
    return (matrix / np.maximum(norms, 1e-9)).astype(np.float32)


@dataclass
class World:
    items: list[dict[str, Any]]
    vectors: np.ndarray
    style: np.ndarray
    colour: np.ndarray
    style_dirs: np.ndarray
    colour_dirs: np.ndarray
    offset: np.ndarray
    sample: np.ndarray
    index: dict[str, int]
    embeddings: dict[str, dict[str, np.ndarray]]


def make_world(seed: int = 0, n_items: int = 3000) -> World:
    rng = np.random.default_rng(seed)
    offset = _unit(rng.normal(size=DIM))
    style_dirs = _unit(rng.normal(size=(N_STYLES, DIM)))
    colour_dirs = _unit(rng.normal(size=(len(COLOURS), DIM)))
    style = rng.integers(N_STYLES, size=n_items)
    colour = rng.integers(len(COLOURS), size=n_items)
    noise = rng.normal(size=(n_items, DIM)) * 0.4 / np.sqrt(DIM)
    vectors = 0.7 * offset + 0.55 * style_dirs[style] + 0.3 * colour_dirs[colour] + noise
    relisted = rng.random(n_items) < 0.15
    sources = rng.integers(n_items, size=n_items)
    for row in np.flatnonzero(relisted):
        base = sources[row]
        style[row], colour[row] = style[base], colour[base]
        vectors[row] = vectors[base] + rng.normal(size=DIM) * 0.12 / np.sqrt(DIM)
    vectors = _unit(vectors)
    weights = np.asarray([kind[2] for kind in KINDS])
    kinds = rng.choice(len(KINDS), size=n_items, p=weights / weights.sum())
    items = []
    for row in range(n_items):
        slot, category, _weight = KINDS[kinds[row]]
        tagged_style = style[row] if rng.random() < 0.75 else rng.integers(N_STYLES)
        secondhand = rng.random() < 0.25
        items.append({
            "id": f"i{row}",
            "title": f"{COLOURS[colour[row]]} {category} {row}",
            "source": "vinted" if secondhand else f"shop{row % 5}",
            "source_url": f"https://shop.example/{row}",
            "image_url": f"https://img.example/{row}.jpg",
            "price_gbp": float(rng.integers(15, 150)),
            "availability": "InStock",
            "fetched_at": "2026-01-01T00:00:00Z",
            "tags": {
                "slot": slot,
                "category": category,
                "gender": "unisex",
                "condition": "second-hand" if secondhand else "new",
                "aesthetics": [f"aesthetic{tagged_style:02d}"],
                "colours": [COLOURS[colour[row]]],
                "niche": 0,
            },
        })
    sample = vectors[rng.choice(n_items, size=min(3000, n_items), replace=False)]
    return World(
        items=items, vectors=vectors, style=style, colour=colour,
        style_dirs=style_dirs, colour_dirs=colour_dirs, offset=offset,
        sample=sample, index={item["id"]: row for row, item in enumerate(items)},
        embeddings={item["id"]: {"mix": vectors[row]} for row, item in enumerate(items)},
    )


@dataclass
class User:
    id: str
    styles: list[int]
    stated_styles: list[int]
    liked_colours: list[int]
    hated_colour: int
    keep: np.ndarray
    brief: dict[str, Any]
    taste: dict[str, Any]
    brief_vector: np.ndarray


def make_user(world: World, seed: int) -> User:
    rng = np.random.default_rng(10_000 + seed)
    n_styles = int(rng.choice([1, 2, 3], p=[0.4, 0.4, 0.2]))
    styles = [int(value) for value in rng.choice(N_STYLES, size=n_styles, replace=False)]
    stated = [styles[0]] + [value for value in styles[1:] if rng.random() < 0.5]
    colours = [int(value) for value in rng.choice(len(COLOURS), size=3, replace=False)]
    liked_colours, hated = colours[:2], colours[2]
    stated_colours = [value for value in liked_colours if rng.random() < 0.5]
    utility = (
        2.4 * np.isin(world.style, styles)
        + 0.9 * np.isin(world.colour, liked_colours)
        - 1.5 * (world.colour == hated)
        + 0.8 * rng.normal(size=len(world.items))
    )
    probability = 1.0 / (1.0 + np.exp(-(utility - 2.2)))
    keep = rng.random(len(world.items)) < probability
    brief = {
        "text": "simulated", "gender": "both", "budget_gbp": 400,
        "condition": "both", "include_shoes": True,
    }
    likes = {f"aesthetic{value:02d}": 0.8 for value in stated}
    likes.update({COLOURS[value]: 0.5 for value in stated_colours})
    avoids = {COLOURS[hated]: 0.6} if rng.random() < 0.5 else {}
    taste = {"likes": likes, "avoids": avoids, "niche_level": 0, "price_ceiling_gbp": 400}
    brief_vector = (
        0.7 * world.offset + 0.55 * world.style_dirs[stated].mean(axis=0)
        + (0.3 * world.colour_dirs[stated_colours].mean(axis=0) if stated_colours else 0.0)
        + rng.normal(size=DIM) * 0.5 / np.sqrt(DIM)
    )
    return User(
        id=f"u{seed}", styles=styles, stated_styles=stated,
        liked_colours=liked_colours, hated_colour=hated, keep=keep,
        brief=brief, taste=taste, brief_vector=_unit(brief_vector),
    )


@dataclass
class Context:
    world: World
    user: User
    swipes: list[dict[str, Any]]
    recent_ids: list[str]
    request: int
    tag_ranked: list[dict[str, Any]]

    def vector(self, item_id: str) -> np.ndarray:
        return self.world.vectors[self.world.index[item_id]]

    def rng(self) -> np.random.Generator:
        text = f"{self.user.id}:{len(self.swipes)}:{self.request}"
        return np.random.default_rng(int(hashlib.sha1(text.encode()).hexdigest()[:16], 16))


def _nearest(
    context: Context, query: np.ndarray, candidates: list[dict], k: int = 400
) -> list[str]:
    rows = [context.world.index[item["id"]] for item in candidates]
    similarities = context.world.vectors[rows] @ np.asarray(query, dtype=np.float32)
    order = np.argsort(-similarities, kind="stable")[:k]
    return [candidates[index]["id"] for index in order]


def _blended_query(context: Context, history: np.ndarray | None, k: float) -> np.ndarray:
    brief = context.user.brief_vector
    if history is None:
        return brief
    _mean, brief_std = zstats(context.world.sample, brief)
    _mean, history_std = zstats(context.world.sample, history)
    weight = k / (k + len(context.swipes))
    return weight * brief / brief_std + (1.0 - weight) * history / history_std


def _swiped_items(context: Context) -> list[tuple[dict, bool]]:
    return [
        (context.world.items[context.world.index[swipe["item_id"]]], swipe["liked"])
        for swipe in context.swipes
    ]


def _plain_vectors(context: Context) -> dict[str, np.ndarray]:
    return {item_id: entry["mix"] for item_id, entry in context.world.embeddings.items()}


def _centroid_raw(context: Context, pool: list[dict], rng, *, k: float, pass_weight: float):
    """Replica of choose_bootdiv's raw score (same RNG call order)."""
    liked = [self_vec for self_vec, keep in _history_vectors(context) if keep]
    passed = [self_vec for self_vec, keep in _history_vectors(context) if not keep]
    liked_weights = rng.poisson(1.0, len(liked))
    passed_weights = rng.poisson(1.0, len(passed))
    query = np.zeros(DIM, dtype=np.float32)
    if liked:
        query += np.sum(np.asarray(liked) * liked_weights[:, None], axis=0) / max(
            int(liked_weights.sum()), 1)
    if passed:
        query -= pass_weight * np.sum(
            np.asarray(passed) * passed_weights[:, None], axis=0
        ) / max(int(passed_weights.sum()), 1)
    weight = k / (k + len(context.swipes)) * rng.gamma(4.0, 0.25)
    vectors = np.asarray([context.vector(item["id"]) for item in pool])
    brief = context.user.brief_vector
    brief_mean, brief_std = zstats(context.world.sample, brief)
    history_mean, history_std = zstats(context.world.sample, query)
    history_z = (vectors @ query - history_mean) / history_std if context.swipes else 0.0
    return (
        weight * (vectors @ brief - brief_mean) / brief_std
        + (1.0 - weight) * history_z
        + _tag_term(context, pool)
    )


def _tag_term(context: Context, pool: list[dict]) -> np.ndarray:
    return np.asarray(
        [PRODUCTION_WEIGHT * score_item(item, context.user.taste) / 2.0 for item in pool]
    )


def _history_vectors(context: Context) -> list[tuple[np.ndarray, bool]]:
    return [(context.vector(swipe["item_id"]), swipe["liked"]) for swipe in context.swipes]


def _topm(similarities: np.ndarray, m: int) -> np.ndarray:
    if similarities.shape[1] == 0:
        return np.zeros(similarities.shape[0], dtype=np.float32)
    m = min(m, similarities.shape[1])
    return np.sort(similarities, axis=1)[:, -m:].mean(axis=1)


def _knn_raw(context: Context, pool: list[dict], rng, *, k: float, pass_weight: float, m: int = 3):
    """Multi-interest score: mean similarity to the m closest kept items (PinnerSage-style)."""
    history = _history_vectors(context)
    liked = np.asarray([vector for vector, keep in history if keep]).reshape(-1, DIM)
    passed = np.asarray([vector for vector, keep in history if not keep]).reshape(-1, DIM)
    liked = liked[rng.poisson(1.0, len(liked)) > 0]
    passed = passed[rng.poisson(1.0, len(passed)) > 0]
    weight = k / (k + len(context.swipes)) * rng.gamma(4.0, 0.25)
    vectors = np.asarray([context.vector(item["id"]) for item in pool])

    def history_score(matrix):
        return _topm(matrix @ liked.T, m) - pass_weight * _topm(matrix @ passed.T, m)

    brief = context.user.brief_vector
    brief_mean, brief_std = zstats(context.world.sample, brief)
    sample_scores = history_score(context.world.sample)
    history_std = float(sample_scores.std()) or 1.0
    history_z = (
        (history_score(vectors) - float(sample_scores.mean())) / history_std
        if len(liked) or len(passed) else 0.0
    )
    return (
        weight * (vectors @ brief - brief_mean) / brief_std
        + (1.0 - weight) * history_z + _tag_term(context, pool)
    )


def _lints_state(context: Context, *, prior_scale: float = 1.0):
    """Bayesian linear regression on centred vectors; prior mean = brief direction."""
    mean = context.world.sample.mean(axis=0)
    centred_sample = context.world.sample - mean
    brief = context.user.brief_vector
    brief_std = float((centred_sample @ brief).std()) or 1.0
    prior_mean = prior_scale * brief / brief_std
    prior_var = 1.0 / float((centred_sample ** 2).sum(axis=1).mean())
    history = _history_vectors(context)
    precision = np.eye(DIM) / prior_var
    target = prior_mean / prior_var
    for vector, keep in history:
        x = vector - mean
        precision += np.outer(x, x)
        target += x * (1.0 if keep else -1.0)
    covariance = np.linalg.inv(precision)
    posterior = covariance @ target
    chol = np.linalg.cholesky(covariance + 1e-9 * np.eye(DIM))
    return mean, posterior, chol


def _lints_raw(context: Context, pool: list[dict], rng, *, state):
    mean, posterior, chol = state
    weights = posterior + chol @ rng.normal(size=DIM)
    vectors = np.asarray([context.vector(item["id"]) for item in pool]) - mean
    return vectors @ weights + _tag_term(context, pool)


def _selector(context: Context, raw_fn: Callable, *, diversity: str, rng):
    recent = [context.vector(item_id) for item_id in context.recent_ids]

    def select(pool, selected):
        values = np.asarray(raw_fn(context, pool, rng), dtype=np.float32)
        window = (recent + [context.vector(item["id"]) for item in selected])[-5:]
        if window:
            vectors = np.asarray([context.vector(item["id"]) for item in pool])
            penalties = np.max(vectors @ np.asarray(window).T, axis=1)
            if diversity == "mmr":
                span = float(values.max() - values.min()) or 1.0
                values = 0.7 * (values - values.min()) / span - 0.3 * penalties / max(
                    float(penalties.max()), 1e-9)
            else:
                values -= penalties * float(values.std())
        if selected:
            vectors = np.asarray([context.vector(item["id"]) for item in pool])
            chosen = np.asarray([context.vector(item["id"]) for item in selected])
            allowed = np.flatnonzero((vectors @ chosen.T).max(axis=1) < LIKE_COS)
            if len(allowed):
                return pool[int(allowed[np.argmax(values[allowed])])]
        return pool[int(np.argmax(values))]

    return select


def _embed_candidates(context: Context, *, retrieval: str, lookalikes: str, k: float):
    tag_ranked = drop_lookalikes(
        context.tag_ranked, _swiped_items(context), _plain_vectors(context),
        vector_window=200 if lookalikes == "prod" else len(context.tag_ranked),
    )
    history = history_query(context.swipes, context.world.embeddings)
    if retrieval == "blend":
        query = _blended_query(context, history, k)
    else:
        query = history if history is not None else context.user.brief_vector
    nearest = _nearest(context, query, tag_ranked)
    if retrieval == "knn":
        liked = [swipe["item_id"] for swipe in context.swipes if swipe["liked"]][-8:]
        for item_id in liked:
            nearest += _nearest(context, context.vector(item_id), tag_ranked, k=25)
        nearest = list(dict.fromkeys(nearest))
    ranked, scores, _history = rank_candidates(
        tag_ranked, context.user.id, context.user.taste, context.user.brief_vector,
        context.swipes, context.world.embeddings, context.world.sample,
        nearest_ids=nearest,
    )
    return ranked, scores


def policy_prod(context: Context) -> list[dict]:
    """Mirror of phase2_api.deck() for the embed arm."""
    ranked, scores = _embed_candidates(context, retrieval="history", lookalikes="prod", k=8.0)
    rng = context.rng()
    recent = [context.vector(item_id) for item_id in context.recent_ids]

    def selector(pool, selected):
        return choose_bootdiv(
            pool, selected, context.swipes, context.world.embeddings, scores,
            context.user.brief_vector, context.user.taste, context.world.sample,
            recent, rng,
        )

    return make_deck(ranked, DECK, context.user.brief, secondhand_share=SHARE, selector=selector)


def embed_policy(
    *, retrieval: str = "history", lookalikes: str = "prod", scorer: str = "centroid",
    diversity: str = "prod", k: float = 8.0, pass_weight: float = 0.5,
) -> Callable[[Context], list[dict]]:
    def policy(context: Context) -> list[dict]:
        old_k = embedding_ranker.BLEND_K
        embedding_ranker.BLEND_K = k
        try:
            ranked, _scores = _embed_candidates(
                context, retrieval=retrieval, lookalikes=lookalikes, k=k
            )
        finally:
            embedding_ranker.BLEND_K = old_k
        rng = context.rng()
        if scorer == "knn":
            def raw(ctx, pool, generator):
                return _knn_raw(ctx, pool, generator, k=k, pass_weight=pass_weight)
        elif scorer == "lints":
            state = _lints_state(context)

            def raw(ctx, pool, generator):
                return _lints_raw(ctx, pool, generator, state=state)
        else:
            def raw(ctx, pool, generator):
                return _centroid_raw(ctx, pool, generator, k=k, pass_weight=pass_weight)
        selector = _selector(context, raw, diversity=diversity, rng=rng)
        return make_deck(
            ranked, DECK, context.user.brief, secondhand_share=SHARE, selector=selector
        )

    return policy


def policy_tags(context: Context) -> list[dict]:
    ranked = drop_lookalikes(context.tag_ranked, _swiped_items(context), _plain_vectors(context))
    deck = make_deck(ranked, DECK, context.user.brief, secondhand_share=SHARE)
    return add_exploration(
        deck, ranked, DECK, context.user.taste, swipes=len(context.swipes),
        seed=f"{context.user.id}:{len(context.swipes)}", brief=context.user.brief,
    )


def policy_random(context: Context) -> list[dict]:
    pool = list(context.tag_ranked)
    context.rng().shuffle(pool)
    return make_deck(pool, DECK, context.user.brief, secondhand_share=SHARE)


POLICIES: dict[str, Callable[[Context], list[dict]]] = {
    "embed (prod)": policy_prod,
    "prod replica": embed_policy(),
    "lookalike filter on all candidates": embed_policy(lookalikes="all"),
    "blended retrieval": embed_policy(retrieval="blend"),
    "blended retrieval + lookalike filter": embed_policy(retrieval="blend", lookalikes="all"),
    "multi-interest kNN": embed_policy(retrieval="knn", lookalikes="all", scorer="knn"),
    "LinTS bandit": embed_policy(retrieval="blend", lookalikes="all", scorer="lints"),
    "MMR diversity": embed_policy(retrieval="blend", lookalikes="all", diversity="mmr"),
    "brief k=4": embed_policy(retrieval="blend", lookalikes="all", k=4.0),
    "brief k=16": embed_policy(retrieval="blend", lookalikes="all", k=16.0),
    "pass weight 1.0": embed_policy(retrieval="blend", lookalikes="all", pass_weight=1.0),
    "tags (prod)": policy_tags,
    "random": policy_random,
}


def simulate(world: World, user: User, policy: Callable[[Context], list[dict]]) -> dict:
    tag_order = rank_items(world.items, user.brief, user.taste)
    swipes: list[dict[str, Any]] = []
    shown: list[str] = []
    swiped_ids: set[str] = set()
    for request in range(REQUESTS):
        context = Context(
            world=world, user=user, swipes=list(swipes), recent_ids=shown[-5:],
            request=request,
            tag_ranked=[item for item in tag_order if item["id"] not in swiped_ids],
        )
        for item in policy(context)[:DECK]:
            row = world.index[item["id"]]
            swipes.append({"item_id": item["id"], "liked": bool(user.keep[row])})
            swiped_ids.add(item["id"])
            shown.append(item["id"])
    return metrics(world, user, swipes)


def metrics(world: World, user: User, swipes: list[dict[str, Any]]) -> dict:
    rows = np.asarray([world.index[swipe["item_id"]] for swipe in swipes])
    kept = np.asarray([swipe["liked"] for swipe in swipes], dtype=float)
    vectors = world.vectors[rows]
    violations = 0
    for position in range(1, len(rows)):
        earlier = vectors[:position] @ vectors[position]
        liked_before = kept[:position] > 0
        if (
            (earlier[liked_before] >= LIKE_COS).any()
            or (earlier[~liked_before] >= DISLIKE_COS).any()
        ):
            violations += 1
    decks = [vectors[start:start + DECK] for start in range(0, len(rows), DECK)]
    similarity = [
        float((deck @ deck.T)[np.triu_indices(len(deck), 1)].mean())
        for deck in decks if len(deck) > 1
    ]
    hidden = [style for style in user.styles if style not in user.stated_styles]
    kept_styles = set(world.style[rows[kept > 0]].tolist())
    return {
        "keep@1-20": float(kept[:20].mean()) if len(kept) else 0.0,
        "keep@21-100": float(kept[20:].mean()) if len(kept) > 20 else 0.0,
        "keep@1-100": float(kept.mean()) if len(kept) else 0.0,
        "styles found": len(kept_styles & set(user.styles)) / len(user.styles),
        "hidden style found": (
            len(kept_styles & set(hidden)) / len(hidden) if hidden else None
        ),
        "lookalike shown": violations / max(1, len(rows)),
        "repeat shown": 1.0 - len(set(rows.tolist())) / max(1, len(rows)),
        "deck similarity": float(np.mean(similarity)) if similarity else 0.0,
        "second-hand share": float(np.mean([
            world.items[row]["tags"]["condition"] == "second-hand" for row in rows
        ])),
        "cards": len(rows),
    }


def _run_user(args: tuple[int, int, int, list[str]]) -> dict[str, dict]:
    world_seed, n_items, user_seed, names = args
    world = make_world(world_seed, n_items)
    user = make_user(world, user_seed)
    return {name: simulate(world, user, POLICIES[name]) for name in names}


def run(users: int = 100, *, n_items: int = 3000, world_seed: int = 0,
        names: list[str] | None = None, workers: int = 8, seed: int = 0) -> dict:
    names = names or list(POLICIES)
    jobs = [(world_seed, n_items, user_seed, names) for user_seed in range(users)]
    if workers > 1:
        with ProcessPoolExecutor(workers) as pool:
            per_user = list(pool.map(_run_user, jobs))
    else:
        per_user = [_run_user(job) for job in jobs]
    return {"users": users, "items": n_items, "summary": summarise(per_user, names, seed=seed)}


def summarise(per_user: list[dict[str, dict]], names: list[str], *, seed: int = 0,
              baseline: str = "embed (prod)", reps: int = 2000) -> dict:
    rng = np.random.default_rng(seed)
    metric_names = [name for name in per_user[0][names[0]] if name != "cards"]
    out: dict[str, dict] = {}
    for name in names:
        out[name] = {}
        for metric in metric_names:
            pairs = [
                (row[name][metric], row[baseline][metric] if baseline in row else None)
                for row in per_user if row[name][metric] is not None
            ]
            values = np.asarray([value for value, _base in pairs], dtype=float)
            draws = rng.integers(len(values), size=(reps, len(values)))
            entry = {
                "mean": float(values.mean()),
                "ci": [float(x) for x in np.percentile(values[draws].mean(axis=1), [2.5, 97.5])],
            }
            if baseline in names and name != baseline:
                diffs = np.asarray(
                    [value - base for value, base in pairs if base is not None], dtype=float
                )
                means = diffs[rng.integers(len(diffs), size=(reps, len(diffs)))].mean(axis=1)
                entry["diff"] = float(diffs.mean())
                entry["diff_ci"] = [float(x) for x in np.percentile(means, [2.5, 97.5])]
            out[name][metric] = entry
    return out


def render(report: dict) -> str:
    summary = report["summary"]
    names = list(summary)
    metrics_order = list(summary[names[0]])
    lines = [
        f"Simulated users: {report['users']}, catalogue {report['items']} items, "
        f"{REQUESTS} decks x {DECK} cards. 95% bootstrap CI over users; "
        "diff = paired vs embed (prod).", "",
        "| policy | " + " | ".join(metrics_order) + " |",
        "|---|" + "---|" * len(metrics_order),
    ]
    for name in names:
        cells = []
        for metric in metrics_order:
            entry = summary[name][metric]
            cell = f"{entry['mean']:.3f}"
            if "diff" in entry:
                low, high = entry["diff_ci"]
                star = "*" if low > 0 or high < 0 else ""
                cell += f" ({entry['diff']:+.3f}{star})"
            cells.append(cell)
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    lines += ["", "* = paired 95% CI excludes 0."]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--users", type=int, default=100)
    parser.add_argument("--items", type=int, default=3000)
    parser.add_argument("--world-seed", type=int, default=0)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--policies", nargs="*")
    parser.add_argument("--json")
    args = parser.parse_args(argv)
    names = args.policies or None
    if names and "embed (prod)" not in names:
        names = ["embed (prod)", *names]
    report = run(args.users, n_items=args.items, world_seed=args.world_seed,
                 names=names, workers=args.workers)
    print(render(report))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)


if __name__ == "__main__":
    main()
