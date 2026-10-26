import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.llm import TaskError, call_task
from app.prompts import DECODER_MODEL, DECODER_SYSTEM

logger = logging.getLogger(__name__)
GLOSSARY_PATH = Path(__file__).resolve().parent / "data" / "style_glossary.json"


class DecoderOut(BaseModel):
    styles: list[str] = Field(default_factory=list)
    unknown_terms: list[str] = Field(default_factory=list)
    confidence: float


@lru_cache(maxsize=1)
def load_glossary() -> list[dict[str, Any]]:
    with GLOSSARY_PATH.open(encoding="utf-8") as glossary_file:
        return json.load(glossary_file)


by_name = {
    entry["name"].casefold(): entry
    for entry in load_glossary()
}


def _alias_pattern(alias: str) -> re.Pattern[str]:
    phrase = r"\s+".join(re.escape(word) for word in alias.strip().split())
    return re.compile(rf"(?<!\w){phrase}s?(?!\w)", re.IGNORECASE)


def match_text(text: str) -> list[str]:
    text = str(text or "")
    matches: list[tuple[int, int, str]] = []
    for index, entry in enumerate(load_glossary()):
        first_position: int | None = None
        for alias in [entry["name"], *entry.get("aliases", [])]:
            if not alias:
                continue
            match = _alias_pattern(alias).search(text)
            if match and (first_position is None or match.start() < first_position):
                first_position = match.start()
        if first_position is not None:
            matches.append((first_position, index, entry["name"]))
    matches.sort()
    return [name for _, _, name in matches[:3]]


def decoder_user_content(brief_text: str) -> str:
    glossary = "\n".join(
        f"{entry['name']}: {', '.join(entry.get('aliases', []))}"
        for entry in load_glossary()
    )
    return f"Glossary:\n{glossary}\n\nBrief:\n{brief_text}"


def decode_brief(brief: dict[str, Any]) -> list[str]:
    brief_text = str(brief.get("text") or "")
    try:
        decoded, _meta = call_task(
            "decoder",
            DECODER_SYSTEM,
            DECODER_MODEL,
            decoder_user_content(brief_text),
            DecoderOut,
        )
        unknown_terms = [term for term in decoded.unknown_terms if term.strip()]
        if unknown_terms:
            logger.info("Unknown style terms from decoder: %s", unknown_terms)

        names: list[str] = []
        seen: set[str] = set()
        for value in decoded.styles:
            entry = by_name.get(value.strip().casefold())
            if entry and entry["name"].casefold() not in seen:
                names.append(entry["name"])
                seen.add(entry["name"].casefold())
        if not decoded.styles:
            for name in match_text(brief_text):
                if name.casefold() not in seen:
                    names.append(name)
                    seen.add(name.casefold())
        return names[:3]
    except TaskError:
        return match_text(brief_text)
    except Exception:
        return match_text(brief_text)


def _referenced_entries(brief: dict[str, Any]) -> list[dict[str, Any]]:
    references = (
        brief.get("style_refs")
        if "style_refs" in brief
        else match_text(str(brief.get("text") or ""))
    )
    entries = []
    seen: set[str] = set()
    for reference in references or []:
        entry = by_name.get(str(reference).strip().casefold())
        if entry and entry["name"].casefold() not in seen:
            entries.append(entry)
            seen.add(entry["name"].casefold())
    return entries


def style_notes(brief: dict[str, Any]) -> list[str]:
    return [
        f"{entry['name']}: pieces {entry['pieces']}; "
        f"brands {entry['brands']}; colours {entry['colours']}"
        for entry in _referenced_entries(brief)
    ]


def style_queries(brief: dict[str, Any]) -> list[str]:
    return [
        query
        for entry in _referenced_entries(brief)
        for query in entry.get("queries", [])[:2]
    ]
