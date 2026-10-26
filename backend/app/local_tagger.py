import json
import os
from collections import Counter
from functools import lru_cache

import numpy as np

from app import db
from app.schemas import ItemTags

LOCAL_TAGGER_ID = "local:clip-knn-v1"
COLOURS = [
    "black", "white", "grey", "navy", "blue", "light blue", "brown", "beige",
    "cream", "green", "olive", "khaki", "red", "burgundy", "pink", "purple",
    "yellow", "orange", "multicolour",
]


def _unit(vector: np.ndarray) -> np.ndarray:
    vector = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 1e-12 else vector


@lru_cache(maxsize=1)
def _colour_prompt_vectors() -> np.ndarray:
    from app.embedding import encode_texts

    vectors = encode_texts([f"a photo of {colour} clothing" for colour in COLOURS])
    return np.asarray([_unit(vector) for vector in vectors], dtype=np.float32)


class LocalTagger:
    def __init__(self, reference: list[tuple[np.ndarray, dict]], k: int = 10):
        if k < 1:
            raise ValueError("k must be positive")
        self.k = k
        self.reference = [
            (_unit(vector), tags) for vector, tags in reference
            if np.linalg.norm(vector) > 1e-12 and isinstance(tags, dict)
        ]
        self.vectors = (
            np.stack([vector for vector, _tags in self.reference])
            if self.reference else np.zeros((0, 0), dtype=np.float32)
        )

    @classmethod
    def from_db(cls, model: str = "clip32", k: int = 10):
        postgres = bool(os.getenv("QUENYA_DATABASE_URL"))
        mix = "e.mix::text AS mix" if postgres else "e.mix"
        with db.db_connection() as connection:
            rows = connection.execute(
                f"""SELECT i.id, i.tags_json, {mix}
                    FROM items AS i
                    JOIN item_embeddings AS e ON e.item_id=i.id
                    WHERE e.model=? AND i.tags_json IS NOT NULL
                      AND COALESCE(i.tags_request_id, '') NOT LIKE 'local:%'""",
                (model,),
            ).fetchall()
        reference = []
        for row in rows:
            tags = row["tags_json"]
            if isinstance(tags, str):
                tags = json.loads(tags)
            vector = db._vector_value(row["mix"])
            if vector is not None and isinstance(tags, dict):
                reference.append((vector, {**tags, "_item_id": row["id"]}))
        return cls(reference, k=k)

    def tag(
        self,
        item: dict,
        image_vec: np.ndarray | None,
        text_vec: np.ndarray,
    ) -> ItemTags:
        if not self.reference:
            raise ValueError("LocalTagger has no GPT-tagged reference items")
        text = _unit(text_vec)
        image = (
            _unit(image_vec)
            if image_vec is not None and np.linalg.norm(image_vec) > 1e-12
            else None
        )
        query = _unit(image + 0.5 * text) if image is not None else text
        similarities = self.vectors @ query
        item_id = item.get("id")
        excluded = (
            [
                index for index, (_vector, tags) in enumerate(self.reference)
                if tags.get("_item_id") == item_id
            ]
            if item_id is not None else []
        )
        if len(excluded) == len(self.reference):
            raise ValueError("LocalTagger has no neighbours after excluding the item")
        similarities[excluded] = -np.inf
        indexes = np.argsort(-similarities, kind="stable")[: self.k]
        neighbours = [self.reference[index][1] for index in indexes]
        scores = similarities[indexes]
        weights = np.maximum(scores, 0.0)
        if not np.any(weights):
            weights = np.ones(len(indexes), dtype=np.float32)

        def vote(field: str):
            totals = Counter()
            for tags, weight in zip(neighbours, weights, strict=True):
                value = tags.get(field)
                if value is not None:
                    totals[value] += float(weight)
            if not totals:
                raise ValueError(f"Reference tags have no values for {field}")
            return totals.most_common(1)[0][0]

        def ranked_values(field: str, limit: int) -> list[str]:
            totals = Counter()
            for tags, weight in zip(neighbours, weights, strict=True):
                values = tags.get(field) or []
                for value in values:
                    totals[str(value).lower()] += float(weight)
            return [value for value, _weight in totals.most_common(limit)]

        hint = str(item.get("gender_hint") or "").strip().lower()
        gender_hints = {
            "m": "menswear", "men": "menswear", "menswear": "menswear",
            "w": "womenswear", "women": "womenswear", "womenswear": "womenswear",
            "u": "unisex", "unisex": "unisex",
        }
        gender = gender_hints[hint] if hint in gender_hints else vote("gender")
        condition_hint = str(item.get("condition_hint") or "").strip().lower()
        condition = (
            condition_hint if condition_hint in {"second-hand", "new"}
            else "second-hand" if item.get("source") == "vinted" else "new"
        )
        slot = vote("slot")
        same_slot = [
            neighbours[index] for index in range(len(neighbours))
            if neighbours[index].get("slot") == slot
        ]
        if not same_slot:
            raise ValueError("No same-slot category in local references")
        category = same_slot[0]["category"]
        formality = round(
            sum(float(tags["formality"]) * float(weight)
                for tags, weight in zip(neighbours, weights, strict=True))
            / float(weights.sum())
        )
        niche = sum(
            float(tags["niche"]) * float(weight)
            for tags, weight in zip(neighbours, weights, strict=True)
        ) / float(weights.sum())
        colours = (
            [COLOURS[int(np.argmax(image @ _colour_prompt_vectors().T))]]
            if image is not None else ranked_values("colours", 1)
        )
        return ItemTags.model_validate({
            "slot": slot,
            "category": category,
            "colours": colours,
            "materials": ranked_values("materials", 2),
            "fit": vote("fit"),
            "era": vote("era"),
            "aesthetics": ranked_values("aesthetics", 3),
            "formality": min(5, max(1, formality)),
            "niche": round(min(1.0, max(0.0, niche)), 2),
            "gender": gender,
            "logo_heavy": bool(vote("logo_heavy")),
            "condition": condition,
            "one_line": str(item.get("title") or "")[:120],
        })
