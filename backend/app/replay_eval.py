"""Offline replay evaluation of deck ranking on exported swipe history.

Input is the anonymised file written by ``python -m app.eval_export``.

Protocol (per style, time-ordered, no leakage):
  for every cutoff m = 0, step, 2*step, ... the policy sees only the style's own
  brief, brief vector, the taste version saved before swipe m, and swipes[:m];
  it then ranks the style's next ``window`` swiped items (the held-out keeps and
  passes). Cutoff 0 is cold start (brief only, first ``window`` cards).
Metrics are averaged per style and their confidence intervals come from a
bootstrap that resamples owners (a tester's styles are not independent).

Usage:
    python -m app.replay_eval quenya-eval.jsonl.gz [--json report.json]
"""

import argparse
import gzip
import hashlib
import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any

import numpy as np

from app.embedding_ranker import rank_candidates
from app.eval_export import decode_vector
from app.rank import (
    DISLIKE_LOOKALIKE_COS,
    LIKE_LOOKALIKE_COS,
    image_key,
    product_key,
    score_item,
    secondhand_share,
    tags_for,
    tokens,
)

COMMON_IMAGE_PRODUCTS = 5


@dataclass(frozen=True)
class Swipe:
    item_id: str
    liked: bool
    created_at: str
    seq: int


@dataclass
class Style:
    id: str
    owner: str
    role: str
    kind: str
    created_at: str
    arm: str | None
    brief: dict[str, Any]
    brief_vector: np.ndarray | None = None
    swipes: list[Swipe] = field(default_factory=list)
    repeat_swipes: int = 0
    tastes: list[tuple[str, int, dict[str, Any]]] = field(default_factory=list)
    deck_events: list[dict[str, Any]] = field(default_factory=list)
    outfits: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Dataset:
    styles: dict[str, Style]
    items: dict[str, dict[str, Any]]
    vectors: dict[str, np.ndarray]
    sample: np.ndarray


@dataclass(frozen=True)
class State:
    style: Style
    history: tuple[Swipe, ...]
    taste: dict[str, Any]
    cutoff_at: str | None


Policy = Callable[[State, list[dict[str, Any]], Dataset], np.ndarray]


def _unit(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 0 else vector


def load_export(path: Path) -> Dataset:
    styles: dict[str, Style] = {}
    items: dict[str, dict[str, Any]] = {}
    vectors: dict[str, np.ndarray] = {}
    sample_ids: list[str] = []
    records: dict[str, list[dict[str, Any]]] = {}
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            records.setdefault(record.pop("kind"), []).append(record)
    for record in records.get("style", []):
        styles[record["style"]] = Style(
            id=record["style"],
            owner=record["owner"],
            role=record.get("role") or "unknown",
            kind=record.get("profile_kind") or record.get("kind") or "user",
            created_at=record.get("created_at") or "",
            arm=record.get("arm"),
            brief=record.get("brief") or {},
        )
    for record in records.get("brief_vector", []):
        if record["style"] in styles:
            styles[record["style"]].brief_vector = decode_vector(record["v"])
    for record in records.get("item", []):
        items[record["id"]] = record
    for record in records.get("vector", []):
        vectors[record["item_id"]] = decode_vector(record["v"])
    for record in records.get("sample", []):
        sample_ids.extend(record.get("item_ids") or [])

    swipes = sorted(records.get("swipe", []), key=lambda r: (r["created_at"], r["seq"]))
    for record in swipes:
        style = styles.get(record["style"])
        if style is None:
            continue
        if any(swipe.item_id == record["item_id"] for swipe in style.swipes):
            style.repeat_swipes += 1
            continue
        style.swipes.append(Swipe(
            record["item_id"], bool(record["liked"]), record["created_at"], record["seq"]
        ))
    for record in records.get("taste", []):
        if record["style"] in styles:
            styles[record["style"]].tastes.append(
                (record["created_at"], int(record["version"]), record["profile"])
            )
    for record in records.get("deck_event", []):
        if record["style"] in styles:
            styles[record["style"]].deck_events.append(record)
    for record in records.get("outfit", []):
        if record["style"] in styles:
            styles[record["style"]].outfits.append(record)
    for style in styles.values():
        style.tastes.sort(key=lambda entry: (entry[0], entry[1]))
        style.deck_events.sort(key=lambda e: (e["created_at"], e["position"]))
    sample = np.asarray(
        [vectors[item_id] for item_id in sample_ids if item_id in vectors],
        dtype=np.float32,
    )
    return Dataset(styles=styles, items=items, vectors=vectors, sample=sample)


def taste_at(style: Style, moment: str | None) -> dict[str, Any]:
    """Latest taste saved strictly before ``moment`` (None = after all swipes)."""
    taste: dict[str, Any] = {}
    for created_at, _version, profile in style.tastes:
        if moment is not None and created_at >= moment:
            break
        taste = profile
    return taste


def state_at(style: Style, cutoff: int) -> State:
    cutoff_at = style.swipes[cutoff].created_at if cutoff < len(style.swipes) else None
    return State(
        style=style,
        history=tuple(style.swipes[:cutoff]),
        taste=taste_at(style, cutoff_at),
        cutoff_at=cutoff_at,
    )


def _swipe_dicts(state: State) -> list[dict[str, Any]]:
    return [{"item_id": s.item_id, "liked": s.liked} for s in state.history]


def _embeddings(ids: list[str], data: Dataset) -> dict[str, dict[str, Any]]:
    return {item_id: {"mix": data.vectors[item_id]} for item_id in ids if item_id in data.vectors}


def tags_policy(state: State, items: list[dict[str, Any]], data: Dataset) -> np.ndarray:
    return np.asarray([score_item(item, state.taste) for item in items], dtype=np.float64)


def embed_policy(state: State, items: list[dict[str, Any]], data: Dataset) -> np.ndarray:
    """The production ``embed`` arm's score (embedding_ranker.rank_candidates)."""
    ids = [item["id"] for item in items] + [s.item_id for s in state.history]
    _ordered, scores, _history = rank_candidates(
        items,
        state.style.id,
        state.taste,
        state.style.brief_vector,
        _swipe_dicts(state),
        _embeddings(ids, data),
        data.sample,
        nearest_ids=[],
    )
    return np.asarray([scores[item["id"]] for item in items], dtype=np.float64)


def _zstats(sample: np.ndarray, query: np.ndarray) -> tuple[float, float]:
    if len(sample) == 0:
        return 0.0, 1.0
    values = sample @ query
    deviation = float(values.std())
    return float(values.mean()), deviation if deviation > 1e-9 else 1.0


def blend_policy(
    *,
    blend_k: float = 8.0,
    pass_weight: float = 0.5,
    tag_weight: float = 0.25,
    history: str = "mean",
    knn: int = 3,
    evidence_weight: float = 0.0,
) -> Policy:
    """Parameterised embed+tags blend; the defaults reproduce ``embed_policy``."""

    def policy(state: State, items: list[dict[str, Any]], data: Dataset) -> np.ndarray:
        brief = state.style.brief_vector
        liked = [
            data.vectors[s.item_id]
            for s in state.history
            if s.liked and s.item_id in data.vectors
        ]
        passed = [
            data.vectors[s.item_id]
            for s in state.history
            if not s.liked and s.item_id in data.vectors
        ]
        weight = blend_k / (blend_k + len(state.history)) if blend_k > 0 else 0.0
        scores = np.asarray(
            [tag_weight * score_item(item, state.taste) for item in items], dtype=np.float64
        )
        if evidence_weight:
            scores += evidence_weight * _evidence_scores(state, items, data)
        vector_rows = [i for i, item in enumerate(items) if item["id"] in data.vectors]
        if not vector_rows:
            return scores
        matrix = np.asarray([data.vectors[items[i]["id"]] for i in vector_rows], dtype=np.float32)
        embed = np.zeros(len(vector_rows), dtype=np.float64)
        if brief is not None:
            mean, std = _zstats(data.sample, brief)
            embed += weight * (matrix @ brief - mean) / std
        if (liked or passed) and state.history:
            if history == "mean":
                query = np.zeros(matrix.shape[1], dtype=np.float32)
                if liked:
                    query += np.mean(liked, axis=0)
                if passed:
                    query -= pass_weight * np.mean(passed, axis=0)
                mean, std = _zstats(data.sample, query)
                embed += (1.0 - weight) * (matrix @ query - mean) / std
            else:
                embed += (1.0 - weight) * _knn_z(
                    matrix, liked, passed, pass_weight, knn, data.sample
                )
        scores[vector_rows] += embed
        return scores

    return policy


def _knn_part(matrix: np.ndarray, anchors: list[np.ndarray], k: int) -> np.ndarray:
    sims = matrix @ np.asarray(anchors, dtype=np.float32).T
    k = min(k, sims.shape[1])
    return np.sort(sims, axis=1)[:, -k:].mean(axis=1)


def _knn_z(matrix, liked, passed, pass_weight, k, sample) -> np.ndarray:
    def raw(rows: np.ndarray) -> np.ndarray:
        value = np.zeros(len(rows), dtype=np.float64)
        if liked:
            value += _knn_part(rows, liked, k)
        if passed:
            value -= pass_weight * _knn_part(rows, passed, k)
        return value

    reference = raw(sample) if len(sample) else raw(matrix)
    std = float(reference.std()) or 1.0
    return (raw(matrix) - float(reference.mean())) / std


def _evidence_scores(state: State, items: list[dict[str, Any]], data: Dataset) -> np.ndarray:
    """Smoothed per-tag keep log-odds learned only from this style's earlier swipes."""
    counts: dict[str, list[int]] = {}
    kept_total = sum(s.liked for s in state.history)
    base = (kept_total + 1) / (len(state.history) + 2)
    for swipe in state.history:
        item = data.items.get(swipe.item_id)
        if item is None:
            continue
        for token in tokens(item):
            entry = counts.setdefault(token, [0, 0])
            entry[0 if swipe.liked else 1] += 1
    prior = 2.0
    scores = []
    for item in items:
        total = 0.0
        for token in tokens(item):
            kept, passed = counts.get(token, (0, 0))
            rate = (kept + prior * base) / (kept + passed + prior)
            total += math.log(rate / (1 - rate)) - math.log(base / (1 - base))
        scores.append(total)
    return np.asarray(scores, dtype=np.float64)


def default_policies() -> dict[str, Policy]:
    return {
        "tags (prod)": tags_policy,
        "embed (prod)": embed_policy,
        "embed k=2": blend_policy(blend_k=2),
        "embed k=4": blend_policy(blend_k=4),
        "embed k=16": blend_policy(blend_k=16),
        "embed pass=0": blend_policy(pass_weight=0.0),
        "embed pass=1": blend_policy(pass_weight=1.0),
        "embed tags=0": blend_policy(tag_weight=0.0),
        "embed tags=1": blend_policy(tag_weight=1.0),
        "embed knn3": blend_policy(history="knn", knn=3, pass_weight=1.0),
        "embed +evidence": blend_policy(evidence_weight=0.5),
        "tags +evidence": blend_policy(blend_k=0, tag_weight=1.0, evidence_weight=0.5,
                                       pass_weight=0.0),
        "brief only": blend_policy(blend_k=1e12, tag_weight=0.0),
    }


def auc(scores: np.ndarray, labels: np.ndarray) -> float | None:
    positives = labels.sum()
    negatives = len(labels) - positives
    if positives == 0 or negatives == 0:
        return None
    order = np.argsort(scores, kind="stable")
    ranks = np.empty(len(scores), dtype=np.float64)
    sorted_scores = scores[order]
    index = 0
    while index < len(scores):
        end = index
        while end + 1 < len(scores) and sorted_scores[end + 1] == sorted_scores[index]:
            end += 1
        ranks[order[index : end + 1]] = (index + end) / 2.0 + 1.0
        index = end + 1
    return float(
        (ranks[labels == 1].sum() - positives * (positives + 1) / 2) / (positives * negatives)
    )


def _tie_key(item_id: str) -> float:
    return int(hashlib.sha1(item_id.encode()).hexdigest()[:8], 16) / 2**32


def precision_at(scores: np.ndarray, labels: np.ndarray, ids: list[str], k: int) -> float:
    order = sorted(range(len(ids)), key=lambda i: (-scores[i], _tie_key(ids[i])))
    top = order[: min(k, len(order))]
    return float(labels[top].mean())


def user_styles(data: Dataset, *, include_admin: bool = True) -> list[Style]:
    return [
        style for style in data.styles.values()
        if style.kind == "user" and style.swipes
        and (include_admin or style.role != "admin")
    ]


def replay(
    data: Dataset,
    policies: dict[str, Policy],
    *,
    window: int = 20,
    step: int = 10,
    min_heldout: int = 5,
    include_admin: bool = True,
) -> list[dict[str, Any]]:
    rows = []
    for style in user_styles(data, include_admin=include_admin):
        for cutoff in range(0, len(style.swipes), step):
            heldout = [
                s for s in style.swipes[cutoff : cutoff + window] if s.item_id in data.items
            ]
            if len(heldout) < min_heldout:
                continue
            state = state_at(style, cutoff)
            items = [data.items[s.item_id] for s in heldout]
            labels = np.asarray([int(s.liked) for s in heldout])
            ids = [s.item_id for s in heldout]
            for name, policy in policies.items():
                scores = policy(state, items, data)
                rows.append({
                    "style": style.id, "owner": style.owner, "cutoff": cutoff,
                    "policy": name, "n": len(heldout), "keeps": int(labels.sum()),
                    "base": float(labels.mean()), "auc": auc(scores, labels),
                    "p5": precision_at(scores, labels, ids, 5),
                })
    return rows


def cluster_bootstrap(
    values: list[tuple[str, float]], *, reps: int = 2000, seed: int = 0
) -> tuple[float, float, float]:
    """Mean of per-style values with a 95% CI from resampling owners."""
    if not values:
        return math.nan, math.nan, math.nan
    by_owner: dict[str, list[float]] = {}
    for owner, value in values:
        by_owner.setdefault(owner, []).append(value)
    owners = list(by_owner)
    sums = np.asarray([sum(by_owner[o]) for o in owners])
    counts = np.asarray([len(by_owner[o]) for o in owners])
    rng = np.random.default_rng(seed)
    picks = rng.integers(0, len(owners), size=(reps, len(owners)))
    means = sums[picks].sum(axis=1) / counts[picks].sum(axis=1)
    mean = float(sums.sum() / counts.sum())
    return mean, float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def bootstrap_p(values: list[tuple[str, float]], *, reps: int = 2000, seed: int = 0) -> float:
    by_owner: dict[str, list[float]] = {}
    for owner, value in values:
        by_owner.setdefault(owner, []).append(value)
    owners = list(by_owner)
    if not owners:
        return math.nan
    sums = np.asarray([sum(by_owner[o]) for o in owners])
    counts = np.asarray([len(by_owner[o]) for o in owners])
    rng = np.random.default_rng(seed)
    picks = rng.integers(0, len(owners), size=(reps, len(owners)))
    means = sums[picks].sum(axis=1) / counts[picks].sum(axis=1)
    return float(min(1.0, 2 * min((means <= 0).mean(), (means >= 0).mean())))


def _segment(cutoff: int) -> str:
    return "cold (cards 1-20)" if cutoff == 0 else "warm (after 10+ swipes)"


def summarise(rows: list[dict[str, Any]], baseline: str = "embed (prod)") -> list[dict[str, Any]]:
    per_style: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (_segment(row["cutoff"]), row["policy"], row["style"], row["owner"])
        per_style.setdefault(key, []).append(row)
    styled: dict[tuple[str, str], dict[str, tuple[str, dict[str, float]]]] = {}
    for (segment, policy, style, owner), entries in per_style.items():
        aucs = [e["auc"] for e in entries if e["auc"] is not None]
        metrics = {
            "p5": float(np.mean([e["p5"] for e in entries])),
            "lift5": float(np.mean([e["p5"] - e["base"] for e in entries])),
            "base": float(np.mean([e["base"] for e in entries])),
        }
        if aucs:
            metrics["auc"] = float(np.mean(aucs))
        styled.setdefault((segment, policy), {})[style] = (owner, metrics)
    summary = []
    for (segment, policy), by_style in sorted(styled.items()):
        reference = styled.get((segment, baseline), {})
        entry: dict[str, Any] = {
            "segment": segment, "policy": policy, "styles": len(by_style),
            "owners": len({owner for owner, _ in by_style.values()}),
        }
        for metric in ("auc", "p5", "lift5", "base"):
            values = [(o, m[metric]) for o, m in by_style.values() if metric in m]
            entry[metric] = cluster_bootstrap(values)
            if policy != baseline and metric in ("auc", "p5"):
                diffs = [
                    (o, m[metric] - reference[s][1][metric])
                    for s, (o, m) in by_style.items()
                    if s in reference and metric in m and metric in reference[s][1]
                ]
                entry[f"d_{metric}"] = cluster_bootstrap(diffs)
                entry[f"p_{metric}"] = bootstrap_p(diffs)
        summary.append(entry)
    return summary


def _image_counts(data: Dataset) -> set[str]:
    products: dict[str, set[str]] = {}
    for item in data.items.values():
        key = image_key(item)
        if key:
            products.setdefault(key, set()).add(product_key(item))
    return {key for key, found in products.items() if len(found) >= COMMON_IMAGE_PRODUCTS}


def logged_stats(data: Dataset, *, include_admin: bool = True) -> dict[str, Any]:
    """What users actually saw (deck_events) and how they reacted."""
    common_images = _image_counts(data)
    keep_values: dict[tuple[str, str], list[tuple[str, float]]] = {}
    repeat_rows: list[tuple[str, float]] = []
    lookalike_rows: list[tuple[str, float]] = []
    diversity_rows: list[tuple[str, float]] = []
    secondhand_gap_rows: list[tuple[str, float]] = []
    exposures = swiped_exposures = 0
    for style in user_styles(data, include_admin=include_admin):
        liked_by_id = {s.item_id: s for s in style.swipes}
        per_arm: dict[tuple[str, str], list[int]] = {}
        repeats = lookalikes = events = 0
        decks: dict[str, list[dict[str, Any]]] = {}
        for event in style.deck_events:
            decks.setdefault(event["created_at"], []).append(event)
        for created_at, deck in decks.items():
            before = [s for s in style.swipes if s.created_at < created_at]
            seen_products = {
                product_key(data.items[s.item_id])
                for s in before
                if s.item_id in data.items
            }
            seen_images = {
                image_key(data.items[s.item_id]) for s in before if s.item_id in data.items
            } - common_images - {None}
            seen_ids = {s.item_id for s in before}
            liked_vectors = [
                data.vectors[s.item_id]
                for s in before
                if s.liked and s.item_id in data.vectors
            ]
            passed_vectors = [
                data.vectors[s.item_id]
                for s in before
                if not s.liked and s.item_id in data.vectors
            ]
            deck_vectors = []
            for event in deck:
                events += 1
                exposures += 1
                item = data.items.get(event["item_id"])
                if event["item_id"] in seen_ids or (
                    item is not None and (
                        product_key(item) in seen_products
                        or (image_key(item) in seen_images)
                    )
                ):
                    repeats += 1
                vector = data.vectors.get(event["item_id"])
                if vector is not None:
                    deck_vectors.append(_unit(vector))
                    if (
                        (
                            passed_vectors
                            and float(np.max(np.asarray(passed_vectors) @ _unit(vector)))
                            >= DISLIKE_LOOKALIKE_COS
                        )
                        or (
                            liked_vectors
                            and float(np.max(np.asarray(liked_vectors) @ _unit(vector)))
                            >= LIKE_LOOKALIKE_COS
                        )
                    ) and event["item_id"] not in seen_ids:
                        lookalikes += 1
                swipe = liked_by_id.get(event["item_id"])
                if swipe is not None and swipe.created_at >= created_at:
                    swiped_exposures += 1
                    for segment in ("all", "cards 1-20" if event["position"] <= 20 else None):
                        if segment:
                            per_arm.setdefault((event["arm"], segment), []).append(int(swipe.liked))
            if len(deck_vectors) >= 2:
                sims = [float(a @ b) for a, b in combinations(deck_vectors, 2)]
                diversity_rows.append((style.owner, 1.0 - float(np.mean(sims))))
            deck_items = [data.items[e["item_id"]] for e in deck if e["item_id"] in data.items]
            if deck_items:
                target = secondhand_share(style.brief, taste_at(style, created_at))
                actual = np.mean([
                    tags_for(i).get("condition") == "second-hand"
                    or (not tags_for(i).get("condition") and i.get("source") == "vinted")
                    for i in deck_items
                ])
                secondhand_gap_rows.append((style.owner, float(actual - target)))
        for key, labels in per_arm.items():
            keep_values.setdefault(key, []).append((style.owner, float(np.mean(labels))))
        if events:
            repeat_rows.append((style.owner, repeats / events))
            lookalike_rows.append((style.owner, lookalikes / events))
    return {
        "exposures": exposures,
        "swiped_exposures": swiped_exposures,
        "keep_rate_by_arm": {
            f"{arm} / {segment}": {"styles": len(values), "rate": cluster_bootstrap(values)}
            for (arm, segment), values in sorted(keep_values.items())
        },
        "repeat_rate": cluster_bootstrap(repeat_rows),
        "lookalike_rate": cluster_bootstrap(lookalike_rows),
        "deck_diversity": cluster_bootstrap(diversity_rows),
        "secondhand_minus_target": cluster_bootstrap(secondhand_gap_rows),
    }


def describe(data: Dataset, *, include_admin: bool = True) -> dict[str, Any]:
    styles = user_styles(data, include_admin=include_admin)
    swipes = [s for style in styles for s in style.swipes]
    referenced = {s.item_id for s in swipes}
    return {
        "owners": len({style.owner for style in styles}),
        "styles": len(styles),
        "swipes": len(swipes),
        "keep_rate": float(np.mean([s.liked for s in swipes])) if swipes else math.nan,
        "repeat_swipes": sum(style.repeat_swipes for style in styles),
        "styles_with_brief_vector": sum(style.brief_vector is not None for style in styles),
        "swiped_items_with_vector": (
            sum(item_id in data.vectors for item_id in referenced) / len(referenced)
            if referenced else math.nan
        ),
        "catalogue_sample": len(data.sample),
        "swipes_per_style": sorted(len(style.swipes) for style in styles),
    }


def _fmt(value: Any) -> str:
    if isinstance(value, tuple):
        mean, low, high = value
        return f"{mean:.3f} [{low:.3f}, {high:.3f}]"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def render(report: dict[str, Any]) -> str:
    lines = ["## Data", "", json.dumps(report["data"], indent=1), ""]
    lines += ["## Replay (rank the next 20 swiped cards)", ""]
    lines.append(
        "| segment | policy | styles | AUC | Δ AUC vs prod embed (p) | "
        "keep@5 | Δ keep@5 (p) | base keep |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")
    for entry in report["replay"]:
        lines.append(
            f"| {entry['segment']} | {entry['policy']} | {entry['styles']} | "
            f"{_fmt(entry['auc'])} | "
            + (f"{_fmt(entry['d_auc'])} ({entry['p_auc']:.2f})" if "d_auc" in entry else "-")
            + f" | {_fmt(entry['p5'])} | "
            + (f"{_fmt(entry['d_p5'])} ({entry['p_p5']:.2f})" if "d_p5" in entry else "-")
            + f" | {_fmt(entry['base'])} |"
        )
    lines += ["", "## Logged decks", ""]
    for key, value in report["logged"].items():
        if isinstance(value, dict):
            for arm, stats in value.items():
                lines.append(f"- keep rate {arm}: {_fmt(stats['rate'])} ({stats['styles']} styles)")
        else:
            lines.append(f"- {key}: {_fmt(value)}")
    return "\n".join(lines)


def run(path: Path, *, include_admin: bool = True) -> dict[str, Any]:
    data = load_export(path)
    rows = replay(data, default_policies(), include_admin=include_admin)
    return {
        "data": describe(data, include_admin=include_admin),
        "replay": summarise(rows),
        "logged": logged_stats(data, include_admin=include_admin),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", type=Path)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--testers-only", action="store_true")
    args = parser.parse_args()
    report = run(args.export, include_admin=not args.testers_only)
    if args.json:
        args.json.write_text(json.dumps(report, indent=1, default=str))
    print(render(report))


if __name__ == "__main__":
    main()
