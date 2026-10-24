import json
import re
from typing import Any

from app import db
from app.mubit import CallMeta, call_task
from app.prompts import TASTE_MODEL, TASTE_SYSTEM
from app.schemas import TasteProfile

_KEY_PREFIX = re.compile(
    r"^(?:style|styles|aesthetic|aesthetics|color|colour|colors|colours|"
    r"category|categories|material|materials|fit|era|brand|brands|slot)"
    r"(?=[:_\-\s])[:_\-\s]+"
)


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


def _normalised_key(key: str) -> str:
    value = _KEY_PREFIX.sub("", str(key).strip().lower(), count=1)
    return " ".join(value.replace("_", " ").replace("-", " ").split())


def _word_boundary_match(left: str, right: str) -> bool:
    left = " ".join(str(left).lower().replace("_", " ").replace("-", " ").split())
    right = " ".join(str(right).lower().replace("_", " ").replace("-", " ").split())
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
        if _word_boundary_match(key, _normalised_key(token))
    ]
    return (
        bool(matching),
        sum(int(counts.get("kept", 0)) for counts in matching),
        sum(int(counts.get("passed", 0)) for counts in matching),
    )


def settle_profile(
    new: dict[str, Any],
    previous: dict[str, Any] | None,
    evidence: dict[str, dict[str, int]],
    explicit_text: str,
    brief_text: str = "",
) -> tuple[dict[str, Any], set[str]]:
    if previous is None:
        return new, set()

    settled = dict(new)
    blocked: set[str] = set()
    for field in ("likes", "avoids"):
        previous_weights = previous.get(field) or {}
        new_weights = new.get(field) or {}
        new_by_key = {
            _normalised_key(key): (key, float(weight))
            for key, weight in new_weights.items()
        }
        previous_keys = {_normalised_key(key) for key in previous_weights}
        weights: dict[str, float] = {}

        for old_key, old_weight in previous_weights.items():
            normalized = _normalised_key(old_key)
            llm_value = new_by_key.get(normalized)
            is_explicit = _word_boundary_match(normalized, explicit_text)
            if is_explicit:
                if llm_value is not None:
                    weights[old_key] = llm_value[1]
                continue
            if llm_value is None:
                decayed = float(old_weight) - 0.3
                if decayed >= 0.1:
                    weights[old_key] = decayed
                continue
            delta = max(-0.3, min(0.3, llm_value[1] - float(old_weight)))
            weights[old_key] = float(old_weight) + delta

        for normalized, (new_key, new_weight) in new_by_key.items():
            if normalized in previous_keys:
                continue
            if _word_boundary_match(normalized, explicit_text):
                weights[new_key] = new_weight
                continue
            mentioned_in_brief = _word_boundary_match(normalized, brief_text)
            seen, kept, passed = _key_evidence(normalized, evidence)
            if field == "likes":
                if not mentioned_in_brief and seen and kept < 2:
                    blocked.add(normalized)
                    continue
            elif (
                not mentioned_in_brief
                and seen
                and (passed < 2 or passed <= kept)
            ):
                blocked.add(normalized)
                continue
            weights[new_key] = min(new_weight, 0.5)

        clamped = {
            key: max(0.0, min(1.0, float(weight)))
            for key, weight in weights.items()
        }
        settled[field] = dict(
            sorted(clamped.items(), key=lambda entry: entry[1], reverse=True)[:15]
        )

    settled["changes"] = [
        phrase
        for phrase in settled.get("changes") or []
        if not any(_word_boundary_match(key, str(phrase)) for key in blocked)
    ]
    return settled, blocked


def create_taste(
    brief: dict[str, Any], profile_id: str
) -> tuple[dict[str, Any], CallMeta]:
    content = taste_user_content(brief, None, 1, [])
    result, meta = call_task("taste", TASTE_SYSTEM, TASTE_MODEL, content, TasteProfile)
    profile = normalize_profile(result, brief)
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
    )
    stored_signals = {
        "lines": lines,
        "consumed_ids": consumed,
    }
    db.save_taste_version(
        profile_id, version, profile, stored_signals, meta.request_id
    )
    return version, profile, meta, stored_signals
