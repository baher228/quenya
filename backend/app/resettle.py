import argparse
from typing import Any

from app import db, taste
from app.rank import norm_token
from app.taste import _compact


def _base_rate(profile_id: str) -> tuple[int, float]:
    counts = db.signal_counts(profile_id)
    swipes = counts["swipes"]
    return swipes, counts["kept"] / swipes if swipes else 0.5


def _reason(
    field: str,
    key: str,
    profile: dict[str, Any],
    evidence: dict[str, dict[str, int]],
    base_rate: float,
    original: str,
) -> tuple[str, int, int, float]:
    seen, kept, passed = taste._key_evidence(key, evidence)
    z = taste._evidence_z(kept, passed, base_rate) if seen else 0.0
    if taste._is_junk_key(original, key):
        reason = "junk"
    else:
        other_field = "avoids" if field == "likes" else "likes"
        opposite_keys = {
            norm_token(value) for value in (profile.get(other_field) or {})
        }
        if key in opposite_keys:
            reason = "contradiction"
        else:
            status = taste._evidence_status(field, kept, passed, base_rate)
            reason = status if status in {"contradicted", "unseen"} else "merged"
    return reason, kept, passed, z


def resettle(board_id: str | None = None, *, apply: bool = False) -> int:
    boards = db.profile_boards("user")
    if board_id:
        boards = [board for board in boards if board["id"] == board_id]
        if not boards:
            raise ValueError(f"No user board found: {board_id}")

    changed_boards = 0
    changed_keys = 0
    removed_keys = 0
    for board in boards:
        latest = db.latest_taste(board["id"])
        if not latest:
            continue
        previous = latest["profile"]
        evidence = db.tag_evidence(board["id"])
        swipes, base_rate = _base_rate(board["id"])
        history_text = "\n".join(db.explicit_history_lines(board["id"]))
        settled, _blocked = taste.settle_profile(
            previous,
            previous,
            evidence,
            "",
            _compact(board["brief"]),
            history_text=history_text,
            base_rate=base_rate,
            mode="cleanup",
        )
        changes = []
        removed = 0
        for field in ("likes", "avoids"):
            current = previous.get(field) or {}
            final = settled.get(field) or {}
            for original, old_weight in current.items():
                key = norm_token(original)
                new_weight = final.get(key)
                if (
                    new_weight is not None
                    and original == key
                    and float(new_weight) == float(old_weight)
                ):
                    continue
                reason, kept, passed, z = _reason(
                    field, key, previous, evidence, base_rate, original
                )
                if new_weight is None:
                    removed += 1
                    new_text = "removed"
                else:
                    new_text = f"{key} {new_weight:.2f}"
                changes.append(
                    f"  {field}: {original} {float(old_weight):.2f} → {new_text}; "
                    f"kept={kept} passed={passed} z={z:.2f} reason={reason}"
                )

        print(
            f"{board.get('label') or board['id']}: swipes={swipes} "
            f"base_rate={base_rate:.3f}"
        )
        if changes:
            print("\n".join(changes))
            changed_boards += 1
            changed_keys += len(changes)
            removed_keys += removed
            print(f"  keys changed: {len(changes)}, removed: {removed}")
            if apply:
                settled["changes"] = [
                    f"Tidied up: dropped {removed} likes/avoids your swipes don't back up"
                ]
                db.save_taste_version(
                    board["id"],
                    int(latest["version"]) + 1,
                    settled,
                    {
                        "lines": [],
                        "consumed_ids": {
                            "swipes": [],
                            "outfits": [],
                            "corrections": [],
                            "uploads": [],
                        },
                        "note": "cleanup",
                    },
                    None,
                )
        else:
            print("  no changes")
            print("  keys changed: 0, removed: 0")
    print(
        f"Boards changed: {changed_boards}; keys changed: {changed_keys}; "
        f"keys removed: {removed_keys}"
    )
    return changed_boards


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Clean up unsupported taste keys")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--board")
    args = parser.parse_args(argv)
    resettle(args.board, apply=args.apply)


if __name__ == "__main__":
    main()
