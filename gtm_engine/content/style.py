"""Style profile extraction (CEO content-quality brief, 2026-10-10): a forensic-linguist
pass over writing samples the user pastes in directly - no LinkedIn scraping - producing a
structured profile that's built once and reused across every future draft instead of
re-derived per post. Deterministic fallback (no samples, or no LLM): a plain, explicitly
non-AI-sounding default (see StyleProfile), never a guess at a style we have no evidence
for."""

from __future__ import annotations

from gtm_engine.content.models import StyleProfile
from gtm_engine.llm.client import LLM, parse_json_object

_PROFILE_FIELDS = (
    "tone", "sentence_pattern", "vocabulary", "rhetorical_habits",
    "punctuation_habits", "formatting_habits", "opener_habits",
)


async def build_style_profile(llm: LLM | None, samples: list[str]) -> StyleProfile:
    cleaned = [s.strip() for s in samples if s and s.strip()]
    if not cleaned:
        return StyleProfile()
    if llm is None:
        return StyleProfile(sample_count=len(cleaned))

    joined = "\n\n---\n\n".join(cleaned[:8])
    system = (
        "You are a forensic linguist. Given writing samples from one author, produce a style "
        "profile detailed enough for someone else to replicate their writing without seeing "
        "the originals. Base every field ONLY on patterns actually present in the samples - "
        "do not invent habits the samples don't show, and do not praise or evaluate the "
        "writing.\n\n"
        "Output a single JSON object with these string keys: tone, sentence_pattern, "
        "vocabulary, rhetorical_habits, punctuation_habits, formatting_habits, opener_habits."
    )
    try:
        raw = await llm.complete(system, joined, max_tokens=500)
    except Exception:
        return StyleProfile(sample_count=len(cleaned))

    obj = parse_json_object(raw) or {}
    profile = StyleProfile(sample_count=len(cleaned))
    for field_name in _PROFILE_FIELDS:
        value = obj.get(field_name)
        if isinstance(value, str) and value.strip():
            setattr(profile, field_name, value.strip())
    return profile
