import hashlib
import logging
import os
from threading import Lock, Thread
from time import monotonic
from typing import Any

import numpy as np

from app.rank import score_item

BLEND_K = 8.0
PRODUCTION_WEIGHT = 0.5
_CATALOGUE_SAMPLE_TTL_S = 3600
_catalogue_cache_lock = Lock()
_catalogue_refresh_in_flight = False
_catalogue_cache: tuple[float, np.ndarray] | None = None
logger = logging.getLogger(__name__)


def fallback_arm(profile_id: str, mode: str | None = None) -> str:
    mode = (mode or os.getenv("QUENYA_RANKER", "ab")).lower()
    if mode in {"tags", "embed"}:
        return mode
    digest = hashlib.sha1(profile_id.encode("utf-8")).digest()
    return "embed" if digest[-1] & 1 else "tags"


def zstats(sample: np.ndarray, query: np.ndarray | None) -> tuple[float, float]:
    if query is None or len(sample) == 0:
        return 0.0, 1.0
    values = sample @ query
    deviation = float(values.std())
    return float(values.mean()), deviation if deviation > 1e-9 else 1.0


def blend_score(
    vector: np.ndarray,
    brief: np.ndarray | None,
    history: np.ndarray | None,
    swipe_count: int,
    taste: dict | None,
    item: dict,
    catalogue: np.ndarray,
) -> float:
    brief_mean, brief_std = zstats(catalogue, brief)
    history_mean, history_std = zstats(catalogue, history)
    brief_z = (float(vector @ brief) - brief_mean) / brief_std if brief is not None else 0.0
    history_z = (
        (float(vector @ history) - history_mean) / history_std
        if history is not None and swipe_count
        else 0.0
    )
    weight = BLEND_K / (BLEND_K + swipe_count)
    return (
        weight * brief_z
        + (1.0 - weight) * history_z
        + PRODUCTION_WEIGHT * score_item(item, taste) / 2.0
    )


def _history_query(swipes: list[dict], embeddings: dict[str, dict]) -> np.ndarray | None:
    liked, passed = [], []
    for swipe in swipes:
        entry = embeddings.get(swipe["item_id"])
        vector = entry.get("mix") if entry else None
        if vector is not None:
            (liked if swipe["liked"] else passed).append(vector)
    if not liked and not passed:
        return None
    dimension = len(liked[0] if liked else passed[0])
    query = np.zeros(dimension, dtype=np.float32)
    if liked:
        query += np.mean(liked, axis=0)
    if passed:
        query -= 0.5 * np.mean(passed, axis=0)
    return query


def history_query(swipes: list[dict], embeddings: dict[str, dict]) -> np.ndarray | None:
    return _history_query(swipes, embeddings)


def _load_catalogue_sample() -> np.ndarray:
    from app import db

    vectors = db.random_item_mix_vectors(3000)
    return np.asarray(vectors, dtype=np.float32)


def _refresh_catalogue_sample() -> None:
    global _catalogue_cache, _catalogue_refresh_in_flight
    try:
        sample = _load_catalogue_sample()
    except Exception:
        logger.exception("Failed to refresh embedding catalogue sample")
    else:
        with _catalogue_cache_lock:
            _catalogue_cache = (monotonic(), sample)
    finally:
        with _catalogue_cache_lock:
            _catalogue_refresh_in_flight = False


def _start_catalogue_refresh_locked() -> None:
    global _catalogue_refresh_in_flight
    _catalogue_refresh_in_flight = True
    try:
        Thread(
            target=_refresh_catalogue_sample,
            daemon=True,
            name="catalogue-sample-refresh",
        ).start()
    except Exception:
        _catalogue_refresh_in_flight = False
        logger.exception("Failed to start embedding catalogue sample refresh")


def catalogue_sample() -> np.ndarray:
    global _catalogue_cache
    with _catalogue_cache_lock:
        if _catalogue_cache is None:
            sample = _load_catalogue_sample()
            _catalogue_cache = (monotonic(), sample)
            return sample

        cached_at, sample = _catalogue_cache
        if (
            monotonic() - cached_at >= _CATALOGUE_SAMPLE_TTL_S
            and not _catalogue_refresh_in_flight
        ):
            _start_catalogue_refresh_locked()
        return sample


def rank_candidates(
    ranked_tags: list[dict[str, Any]],
    profile_id: str,
    taste: dict | None,
    brief_vector: np.ndarray | None,
    swipes: list[dict],
    embeddings: dict[str, dict],
    sample: np.ndarray | None = None,
    nearest_ids: list[str] | None = None,
) -> tuple[list[dict], dict[str, float], np.ndarray | None]:
    sample = catalogue_sample() if sample is None else sample
    history = _history_query(swipes, embeddings)
    swipe_count = len(swipes)
    brief_mean, brief_std = zstats(sample, brief_vector)
    history_mean, history_std = zstats(sample, history)
    scores = {}
    for item in ranked_tags:
        entry = embeddings.get(item["id"])
        vector = entry.get("mix") if entry else None
        if vector is None:
            scores[item["id"]] = PRODUCTION_WEIGHT * score_item(item, taste) / 2.0
        else:
            brief_z = (
                (float(vector @ brief_vector) - brief_mean) / brief_std
                if brief_vector is not None else 0.0
            )
            history_z = (
                (float(vector @ history) - history_mean) / history_std
                if history is not None and swipe_count else 0.0
            )
            weight = BLEND_K / (BLEND_K + swipe_count)
            scores[item["id"]] = (
                weight * brief_z + (1.0 - weight) * history_z
                + PRODUCTION_WEIGHT * score_item(item, taste) / 2.0
            )
    item_by_id = {item["id"]: item for item in ranked_tags}
    if nearest_ids is None:
        vector_candidates = [
            item for item in ranked_tags
            if (embeddings.get(item["id"]) or {}).get("mix") is not None
        ]
        query = history if history is not None else brief_vector
        if query is not None:
            vector_candidates.sort(
                key=lambda item: float(
                    embeddings[item["id"]]["mix"] @ query
                ),
                reverse=True,
            )
    else:
        vector_candidates = [
            item_by_id[item_id]
            for item_id in nearest_ids
            if item_id in item_by_id
            and (embeddings.get(item_id) or {}).get("mix") is not None
        ]
    candidates = {item["id"]: item for item in ranked_tags[:100]}
    candidates.update({item["id"]: item for item in vector_candidates[:400]})
    ordered = sorted(
        candidates.values(), key=lambda item: scores[item["id"]], reverse=True
    )
    if not any((embeddings.get(item["id"]) or {}).get("mix") is not None for item in ordered):
        ordered = ranked_tags
    return ordered, scores, history


def choose_bootdiv(
    candidates: list[dict],
    selected: list[dict],
    swipes: list[dict],
    embeddings: dict[str, dict],
    scores: dict[str, float],
    brief_vector: np.ndarray | None,
    taste: dict | None,
    sample: np.ndarray,
    recent_vectors: list[np.ndarray],
    rng: np.random.Generator,
) -> dict:
    liked, passed = [], []
    for swipe in swipes:
        entry = embeddings.get(swipe["item_id"])
        vector = entry.get("mix") if entry else None
        if vector is not None:
            (liked if swipe["liked"] else passed).append(vector)
    liked_weights = rng.poisson(1.0, len(liked))
    passed_weights = rng.poisson(1.0, len(passed))
    n_liked = max(int(liked_weights.sum()), 1)
    n_passed = max(int(passed_weights.sum()), 1)
    dimension = (
        len(brief_vector) if brief_vector is not None
        else len(sample[0]) if len(sample)
        else len(next(
            (entry["mix"] for entry in embeddings.values() if entry.get("mix") is not None),
            np.zeros(512, dtype=np.float32),
        ))
    )
    query = np.zeros(dimension, dtype=np.float32)
    if liked:
        query += np.sum(np.asarray(liked) * liked_weights[:, None], axis=0) / n_liked
    if passed:
        query -= 0.5 * np.sum(np.asarray(passed) * passed_weights[:, None], axis=0) / n_passed
    weight = BLEND_K / (BLEND_K + len(swipes)) * rng.gamma(4.0, 0.25)
    brief_mean, brief_std = zstats(sample, brief_vector)
    history_mean, history_std = zstats(sample, query)
    candidate_scores = []
    for item in candidates:
        vector = (embeddings.get(item["id"]) or {}).get("mix")
        if vector is None:
            raw = scores[item["id"]]
        else:
            brief_z = (
                (float(vector @ brief_vector) - brief_mean) / brief_std
                if brief_vector is not None else 0.0
            )
            history_z = (
                (float(vector @ query) - history_mean) / history_std
                if swipes else 0.0
            )
            raw = (
                weight * brief_z + (1.0 - weight) * history_z
                + PRODUCTION_WEIGHT * score_item(item, taste) / 2.0
            )
        candidate_scores.append(raw)
    values = np.asarray(candidate_scores, dtype=np.float32)
    diversity_vectors = (recent_vectors + [
        (embeddings.get(item["id"]) or {}).get("mix") for item in selected
    ])[-5:]
    if diversity_vectors:
        pool_vectors = [((embeddings.get(item["id"]) or {}).get("mix")) for item in candidates]
        penalties = np.zeros(len(candidates), dtype=np.float32)
        for recent in diversity_vectors:
            if recent is None:
                continue
            penalties = np.maximum(
                penalties,
                np.asarray([
                    float(vector @ recent) if vector is not None else 0.0
                    for vector in pool_vectors
                ], dtype=np.float32),
            )
        values -= penalties * float(values.std())
    return candidates[int(np.argmax(values))]
