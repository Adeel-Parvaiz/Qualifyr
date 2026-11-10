"""Lay-prompt to ContentBrief (CEO content-quality brief, 2026-10-10). Same shape as
gtm_engine/campaign/nl_parser.py: a deterministic stage that always runs, no LLM needed,
followed by an optional LLM stage that splits the raw text into topic/audience/notes.
Deterministic wins when the LLM stage is unavailable or fails - the brief is never empty,
it just stays coarse (the whole prompt as both topic and notes)."""

from __future__ import annotations

from gtm_engine.content.models import ContentBrief
from gtm_engine.llm.client import LLM, parse_json_object

_TYPE_HINTS: dict[str, list[str]] = {
    "linkedin_comment": ["comment", "reply to a post", "reply to this post"],
    "blog": ["blog", "article", "long-form"],
    "linkedin_post": ["linkedin post", "post", "script"],
}


def _detect_type(text: str) -> str:
    low = text.lower()
    for content_type, hints in _TYPE_HINTS.items():
        if any(hint in low for hint in hints):
            return content_type
    return "linkedin_post"


def parse_content_brief(text: str, source_material: list[str] | None = None) -> ContentBrief:
    """Deterministic stage: the whole prompt becomes the topic and the notes until the LLM
    stage (or a future dedicated brief form) splits it more precisely."""
    text = (text or "").strip()
    return ContentBrief(
        content_type=_detect_type(text),
        topic=text,
        notes=text,
        source_material=list(source_material or []),
        raw_text=text,
    )


async def refine_content_brief(llm: LLM | None, brief: ContentBrief) -> ContentBrief:
    """LLM stage: splits the raw prompt into topic/audience/notes. Deterministic fallback:
    the brief from parse_content_brief() is returned unchanged."""
    if llm is None or not brief.raw_text.strip():
        return brief
    system = (
        "Split this content request into structured fields. Output a single JSON object "
        "with string keys: content_type (one of linkedin_post, linkedin_comment, blog), "
        "topic (what the piece is about, one sentence), audience (who it's for), notes "
        "(anything else the user said - constraints, angle, facts or data they provided). "
        "Never invent information not present in the request; leave a field empty if the "
        "request doesn't say."
    )
    try:
        raw = await llm.complete(system, brief.raw_text, max_tokens=300)
    except Exception:
        return brief
    obj = parse_json_object(raw) or {}
    if isinstance(obj.get("content_type"), str) and obj["content_type"] in _TYPE_HINTS:
        brief.content_type = obj["content_type"]
    if isinstance(obj.get("topic"), str) and obj["topic"].strip():
        brief.topic = obj["topic"].strip()
    if isinstance(obj.get("audience"), str) and obj["audience"].strip():
        brief.audience = obj["audience"].strip()
    if isinstance(obj.get("notes"), str) and obj["notes"].strip():
        brief.notes = obj["notes"].strip()
    return brief
