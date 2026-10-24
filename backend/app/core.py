import re
from collections import defaultdict
from typing import Any

from app import db


def _rule_tokens(value: str) -> set[str]:
    return set(re.sub(r"[\W_]+", " ", value.lower()).split())


def _jaccard(left: set[str], right: set[str]) -> float:
    return len(left & right) / len(left | right) if left | right else 0.0


def shared_core(kind: str = "user") -> dict[str, Any]:
    boards = db.profile_boards(kind)
    result: dict[str, Any] = {
        "rules": [],
        "likes": {},
        "avoids": {},
        "palette": [],
        "sizes": {},
    }
    if len(boards) < 2:
        return result

    rule_entries = []
    for board in boards:
        taste = board.get("taste") or {}
        for rule in taste.get("rules") or []:
            rule_entries.append(
                {
                    "text": str(rule).strip(),
                    "tokens": _rule_tokens(str(rule)),
                    "board_id": board["id"],
                    "label": board.get("label") or "Your style",
                }
            )
    parents = list(range(len(rule_entries)))

    def root(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    for left in range(len(rule_entries)):
        for right in range(left + 1, len(rule_entries)):
            if (
                rule_entries[left]["board_id"] != rule_entries[right]["board_id"]
                and _jaccard(rule_entries[left]["tokens"], rule_entries[right]["tokens"]) >= 0.6
            ):
                parents[root(right)] = root(left)
    groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for index, entry in enumerate(rule_entries):
        groups[root(index)].append(entry)
    for entries in groups.values():
        labels = list(dict.fromkeys(entry["label"] for entry in entries))
        if len({entry["board_id"] for entry in entries}) < 2:
            continue
        wording = min(
            (entry["text"] for entry in entries),
            key=lambda text: (len(text), text.lower()),
        )
        result["rules"].append({"rule": wording, "boards": labels})

    votes: dict[str, dict[str, dict[str, float]]] = defaultdict(
        lambda: {"likes": {}, "avoids": {}}
    )
    palette_boards: dict[str, set[str]] = defaultdict(set)
    for board in boards:
        taste = board.get("taste") or {}
        for field in ("likes", "avoids"):
            for key, weight in (taste.get(field) or {}).items():
                normalized = str(key).strip().lower()
                if normalized and float(weight) > 0:
                    votes[normalized][field][board["id"]] = float(weight)
        for colour in taste.get("palette") or []:
            normalized = str(colour).strip().lower()
            if normalized:
                palette_boards[normalized].add(board["id"])
    for key, signs in votes.items():
        like_boards, avoid_boards = signs["likes"], signs["avoids"]
        if len(like_boards) >= 2:
            result["likes"][key] = sum(like_boards.values()) / len(like_boards) * 0.5
        if len(avoid_boards) >= 2:
            result["avoids"][key] = sum(avoid_boards.values()) / len(avoid_boards) * 0.5
    result["palette"] = sorted(
        colour for colour, board_ids in palette_boards.items() if len(board_ids) >= 2
    )
    latest_board = max(
        boards,
        key=lambda board: board.get("updated_at") or board.get("created_at") or "",
    )
    result["sizes"] = dict(latest_board.get("brief", {}).get("sizes") or {})
    return result


def as_profile(core: dict[str, Any]) -> dict[str, Any]:
    return {
        "likes": core["likes"],
        "avoids": core["avoids"],
        "rules": [item["rule"] for item in core["rules"]],
        "palette": core["palette"],
        "niche_level": 0.0,
    }
