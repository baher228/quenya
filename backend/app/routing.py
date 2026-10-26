import json
import re
from typing import Any

from app import db
from app.llm import CallMeta, call_task
from app.prompts import ROUTER_MODEL, ROUTER_SYSTEM
from app.schemas import RouteOut


def _plain(value: str, limit: int) -> str:
    return re.sub(r"\s+", " ", value).strip()[:limit]


def router_user_content(
    input_kind: str,
    input_text: str,
    boards: list[dict[str, Any]],
    past_corrections: list[dict[str, Any]],
) -> str:
    sections = ["Existing boards:"]
    if boards:
        for board in boards[:12]:
            brief_text = _plain(str(board.get("brief", {}).get("text", "")), 120)
            sections.append(
                f"- id={board['id']} | label={board.get('label') or 'Your style'} "
                f"| context={board.get('context') or 'other'} | brief={brief_text}"
            )
    else:
        sections.append("none")
    sections.append("Past corrections:")
    if past_corrections:
        for correction in past_corrections[:8]:
            target = correction.get("corrected_to", {})
            text = _plain(str(correction.get("input_text", "")), 100)
            quoted = json.dumps(text, ensure_ascii=False)
            sections.append(
                f"- {quoted} -> context={target.get('context', 'other')} "
                f"| label={target.get('label', 'Your style')}"
            )
    else:
        sections.append("none")
    sections.extend((f"New {input_kind}:", input_text))
    return "\n".join(sections)


def route_input(
    input_kind: str, input_text: str, *, kind: str = "user"
) -> tuple[RouteOut, dict[str, Any] | None, CallMeta, str]:
    boards = db.profile_boards(kind)[:12]
    past_corrections = db.corrected_routes(kind, 8)
    content = router_user_content(input_kind, input_text, boards, past_corrections)
    output, meta = call_task(
        "router", ROUTER_SYSTEM, ROUTER_MODEL, content, RouteOut
    )
    board = next(
        (item for item in boards if item["id"] == output.board_id),
        None,
    )
    if not board or output.confidence < 0.6:
        return output, None, meta, content
    output = output.model_copy(
        update={
            "context": board.get("context") or "other",
            "label": board.get("label") or "Your style",
        }
    )
    return output, board, meta, content
