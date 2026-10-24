import json
import math
import re
from typing import Any

from app import db
from app.mubit import CallMeta, call_task
from app.prompts import TASTE_MODEL, TASTE_SYSTEM
from app.rank import norm_token
from app.schemas import TasteProfile

SUPPORT_Z = 1.5
CONTRA_Z = 0.5
CONTRA_MIN_N = 4
_FIELD_NAMES = frozenset(
    {
        "aesthetic",
        "aesthetics",
        "style",
        "styles",
        "colour",
        "colours",
        "color",
        "colors",
        "material",
        "materials",
        "category",
        "categories",
        "fit",
        "era",
        "brand",
        "brands",
        "pattern",
        "patterns",
    }
)
_FILTER_WORDS = frozenset(
    {
        "outerwear",
        "top",
        "tops",
        "bottom",
        "bottoms",
        "knitwear",
        "shoes",
        "accessories",
        "menswear",
        "womenswear",
        "unisex",
    }
)
_CONDITION_GENDER_PREFIX = re.compile(r"^(?:condition|gender)(?:[:_\-\s])")


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _signal_lines(
    signals: dict[str, list[dict[str, Any]]]
) -> tuple[list[str], dict[str, list[str]], list[str]]:
    lines: list[str] = []
    explicit_lines: list[str] = []
    consumed: dict[str, list[str]] = {
        "swipes": [],
        "outfits": [],
        "corrections": [],
        "uploads": [],
    }
    for swipe in signals["swipes"]:
        tags = swipe["tags"]
        label = "kept" if swipe["liked"] else "passed"
        lines.append(
            f"- {label}: {swipe['title']} | {tags.get('category', '')} | "
            f"{', '.join(tags.get('aesthetics', []))} | "
            f"{', '.join(tags.get('colours', []))} | niche {float(tags.get('niche', 0)):.1f} | "
            f"£{float(swipe['price_gbp'] or 0):.0f}"
        )
        consumed["swipes"].append(swipe["id"])
    for outfit in signals["outfits"]:
        pieces = [
            db.get_item_with_tags(item_id)
            for item_id in json.loads(outfit["piece_ids_json"])
        ]
        categories = [
            (item.get("tags") or {}).get("category", item["title"])
            for item in pieces
            if item
        ]
        action = "liked" if outfit["verdict"] == "like" else "rejected"
        line = f'- outfit {action} "{outfit["title"]}": {" + ".join(categories)}'
        if outfit.get("reason"):
            line += f' | reason: "{outfit["reason"]}"'
        signal_line = "- " + line[2:]
        lines.append(signal_line)
        explicit_lines.append(signal_line)
        consumed["outfits"].append(outfit["id"])
    for correction in signals["corrections"]:
        if correction["kind"] == "brief":
            signal_line = f'- brief: "{correction["note"]}"'
        else:
            title = correction.get("title") or correction["target_id"]
            signal_line = (
                f'- tag correction on "{title}": '
                f'"{correction["note"]}"'
            )
        lines.append(signal_line)
        explicit_lines.append(signal_line)
        consumed["corrections"].append(correction["id"])
    for upload in signals["uploads"]:
        read = upload["read"]
        signal_line = (
            f'- inspiration photo: {read.get("one_line", "")} | '
            f'{", ".join(read.get("aesthetics", []))} | '
            f'palette {", ".join(read.get("palette", []))}'
        )
        lines.append(signal_line)
        explicit_lines.append(signal_line)
        consumed["uploads"].append(upload["id"])
    return lines, consumed, explicit_lines


def taste_user_content(
    brief: dict[str, Any],
    current: dict[str, Any] | None,
    version: int,
    lines: list[str],
    *,
    evidence: dict[str, dict[str, int]] | None = None,
) -> str:
    sections = [
        f"Brief:\n{_compact(brief)}",
        f"Current profile (v{version}):\n{_compact(current) if current else 'none'}",
    ]
    if lines:
        sections.append("New signals:\n" + "\n".join(lines))
    if evidence:
        top_evidence = sorted(
            evidence.items(),
            key=lambda entry: (
                -(int(entry[1].get("kept", 0)) + int(entry[1].get("passed", 0))),
                entry[0],
            ),
        )[:25]
        if top_evidence:
            sections.append(
                "Evidence so far (all swipes on this style, kept/passed per tag):\n"
                + ", ".join(
                    f"{token} {counts.get('kept', 0)}/{counts.get('passed', 0)}"
                    for token, counts in top_evidence
                )
            )
    return "\n\n".join(sections)


def normalize_profile(profile: TasteProfile, brief: dict[str, Any]) -> dict[str, Any]:
    data = profile.model_dump()
    for field in ("likes", "avoids"):
        weights = {
            key: max(0.0, min(1.0, float(value)))
            for key, value in data[field].items()
        }
        data[field] = dict(
            sorted(weights.items(), key=lambda entry: entry[1], reverse=True)[:15]
        )
    if data["price_ceiling_gbp"] <= 0:
        data["price_ceiling_gbp"] = float(brief["budget_gbp"])
    if brief["condition"] != "both":
        data["condition_preference"] = brief["condition"]
    return data


_normalised_key = norm_token


def _word_boundary_match(left: str, right: str) -> bool:
    left = norm_token(left)
    right = norm_token(right)
    if not left or not right:
        return False
    return bool(
        re.search(r"\b" + re.escape(left) + r"\b", right)
        or re.search(r"\b" + re.escape(right) + r"\b", left)
    )


def _key_evidence(
    key: str, evidence: dict[str, dict[str, int]]
) -> tuple[bool, int, int]:
    matching = [
        counts
        for token, counts in evidence.items()
        if _word_boundary_match(norm_token(key), norm_token(token))
    ]
    return (
        bool(matching),
        sum(int(counts.get("kept", 0)) for counts in matching),
        sum(int(counts.get("passed", 0)) for counts in matching),
    )


def _evidence_z(kept: int, passed: int, base_rate: float) -> float:
    n = kept + passed
    if n == 0 or base_rate <= 0 or base_rate >= 1:
        return 0.0
    return (kept - base_rate * n) / math.sqrt(n * base_rate * (1 - base_rate))


def _evidence_status(
    field: str,
    kept: int,
    passed: int,
    base_rate: float,
) -> str:
    n = kept + passed
    if n == 0:
        return "unseen"
    z = _evidence_z(kept, passed, base_rate)
    if field == "likes":
        if kept >= 2 and z >= SUPPORT_Z:
            return "supported"
        if n >= CONTRA_MIN_N and z <= -CONTRA_Z:
            return "contradicted"
    else:
        if passed >= 3 and z <= -SUPPORT_Z:
            return "supported"
        if n >= CONTRA_MIN_N and z >= CONTRA_Z:
            return "contradicted"
    return "weak"


def _is_junk_key(original: str, canonical: str) -> bool:
    original_key = str(original).strip().lower()
    return (
        not canonical
        or canonical in _FIELD_NAMES
        or canonical in _FILTER_WORDS
        or bool(_CONDITION_GENDER_PREFIX.match(original_key))
        or canonical.startswith(("condition ", "gender "))
    )


def _canonical_weights(weights: dict[str, Any]) -> dict[str, float]:
    canonical: dict[str, float] = {}
    for key, weight in weights.items():
        token = norm_token(key)
        if token and not _is_junk_key(key, token):
            canonical[token] = max(canonical.get(token, 0.0), float(weight))
    return canonical


def clean_keys(
    profile: dict[str, Any],
    evidence: dict[str, dict[str, int]] | None = None,
    base_rate: float = 0.5,
) -> dict[str, Any]:
    cleaned = dict(profile)
    evidence = evidence or {}
    for field in ("likes", "avoids"):
        weights: dict[str, float] = {}
        for original, weight in (profile.get(field) or {}).items():
            canonical = norm_token(original)
            if _is_junk_key(original, canonical):
                continue
            value = max(0.0, min(1.0, float(weight)))
            weights[canonical] = max(weights.get(canonical, 0.0), value)
        cleaned[field] = weights

    likes = cleaned["likes"]
    avoids = cleaned["avoids"]
    for key in set(likes) & set(avoids):
        seen, kept, passed = _key_evidence(key, evidence)
        z = _evidence_z(kept, passed, base_rate) if seen else 0.0
        if seen and z > 0:
            del avoids[key]
        elif seen and z < 0:
            del likes[key]
        elif likes[key] >= avoids[key]:
            del avoids[key]
        else:
            del likes[key]

    for field in ("likes", "avoids"):
        cleaned[field] = dict(
            sorted(cleaned[field].items(), key=lambda entry: entry[1], reverse=True)[:15]
        )
    return cleaned


def settle_profile(
    new: dict[str, Any],
    previous: dict[str, Any] | None,
    evidence: dict[str, dict[str, int]],
    explicit_text: str,
    brief_text: str = "",
    *,
    history_text: str = "",
    base_rate: float = 0.5,
    mode: str = "update",
) -> tuple[dict[str, Any], set[str]]:
    if mode not in {"update", "cleanup"}:
        raise ValueError(f"Unsupported settlement mode: {mode}")
    if previous is None:
        return clean_keys(new, evidence, base_rate), set()

    settled = dict(new)
    blocked: set[str] = set()
    for field in ("likes", "avoids"):
        previous_weights = _canonical_weights(previous.get(field) or {})
        new_weights = _canonical_weights(new.get(field) or {})
        weights: dict[str, float] = {}

        for key, old_weight in previous_weights.items():
            llm_value = new_weights.get(key)
            is_explicit = _word_boundary_match(key, explicit_text)
            is_protected = (
                is_explicit
                or _word_boundary_match(key, brief_text)
                or _word_boundary_match(key, history_text)
            )
            if is_explicit:
                if llm_value is None:
                    blocked.add(key)
                else:
                    weights[key] = llm_value
                continue
            _, kept, passed = _key_evidence(key, evidence)
            status = _evidence_status(field, kept, passed, base_rate)
            if is_protected:
                if llm_value is None:
                    decayed = old_weight - 0.3
                    if decayed >= 0.1:
                        weights[key] = decayed
                    else:
                        blocked.add(key)
                else:
                    delta = max(-0.3, min(0.3, llm_value - old_weight))
                    weights[key] = old_weight + delta
                continue

            if status == "supported":
                if llm_value is None:
                    decayed = old_weight - 0.3
                    if decayed >= 0.1:
                        weights[key] = decayed
                    else:
                        blocked.add(key)
                else:
                    delta = max(-0.3, min(0.3, llm_value - old_weight))
                    weights[key] = old_weight + delta
            elif status in ("weak", "unseen"):
                if llm_value is None:
                    decayed = old_weight - 0.3
                    if decayed >= 0.1:
                        weights[key] = decayed
                    else:
                        blocked.add(key)
                else:
                    weights[key] = min(old_weight, llm_value)
            elif mode == "update":
                decayed = old_weight - 0.3
                if decayed >= 0.1:
                    weights[key] = decayed
                else:
                    blocked.add(key)
            else:
                blocked.add(key)

        for key, new_weight in new_weights.items():
            if key in previous_weights:
                continue
            if _word_boundary_match(key, explicit_text):
                weights[key] = new_weight
                continue
            protected = (
                _word_boundary_match(key, brief_text)
                or _word_boundary_match(key, history_text)
            )
            if protected:
                weights[key] = min(new_weight, 0.5)
                continue
            _, kept, passed = _key_evidence(key, evidence)
            if _evidence_status(field, kept, passed, base_rate) != "supported":
                blocked.add(key)
                continue
            weights[key] = min(new_weight, 0.5)

        clamped = {
            key: max(0.0, min(1.0, float(weight)))
            for key, weight in weights.items()
        }
        settled[field] = dict(
            sorted(clamped.items(), key=lambda entry: entry[1], reverse=True)[:15]
        )

    cleaned = clean_keys(settled, evidence, base_rate)
    for field in ("likes", "avoids"):
        kept_keys = set(cleaned[field])
        blocked.update(
            norm_token(key)
            for key in (settled.get(field) or {})
            if norm_token(key) not in kept_keys
        )
    cleaned["changes"] = [
        phrase
        for phrase in settled.get("changes") or []
        if not any(_word_boundary_match(key, str(phrase)) for key in blocked)
    ]
    return cleaned, blocked


def create_taste(
    brief: dict[str, Any], profile_id: str
) -> tuple[dict[str, Any], CallMeta]:
    content = taste_user_content(brief, None, 1, [])
    result, meta = call_task("taste", TASTE_SYSTEM, TASTE_MODEL, content, TasteProfile)
    profile = clean_keys(normalize_profile(result, brief))
    db.save_taste_version(
        profile_id,
        1,
        profile,
        {"swipes": [], "outfits": [], "corrections": [], "uploads": []},
        meta.request_id,
    )
    return profile, meta


def update_taste(
    profile_row: dict[str, Any], *, require_signals: bool = True
) -> tuple[int, dict[str, Any], CallMeta, dict[str, Any]]:
    profile_id = profile_row["id"]
    current_row = db.latest_taste(profile_id)
    current_version = int(current_row["version"]) if current_row else 0
    version = current_version + 1
    current = current_row["profile"] if current_row else None
    signals = db.unconsumed_signals(profile_id)
    lines, consumed, _explicit_lines = _signal_lines(signals)
    if require_signals and not lines:
        raise ValueError("No unconsumed taste signals")
    evidence = db.tag_evidence(profile_id)
    counts = db.signal_counts(profile_id)
    base_rate = (
        counts["kept"] / counts["swipes"] if counts["swipes"] else 0.5
    )
    history_text = "\n".join(db.explicit_history_lines(profile_id))
    content = taste_user_content(
        profile_row["brief"], current, current_version, lines, evidence=evidence
    )
    result, meta = call_task("taste", TASTE_SYSTEM, TASTE_MODEL, content, TasteProfile)
    profile = normalize_profile(result, profile_row["brief"])
    explicit_text = "\n".join(_explicit_lines)
    profile, _blocked = settle_profile(
        profile,
        current,
        evidence,
        explicit_text,
        brief_text=_compact(profile_row["brief"]),
        history_text=history_text,
        base_rate=base_rate,
    )
    stored_signals = {
        "lines": lines,
        "consumed_ids": consumed,
    }
    db.save_taste_version(
        profile_id, version, profile, stored_signals, meta.request_id
    )
    return version, profile, meta, stored_signals
