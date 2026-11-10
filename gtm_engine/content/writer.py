"""Content writer (CEO content-quality brief, 2026-10-10): ties the four mechanisms
together - context brief, style profile, verified claims, AI-pattern check - so a draft only
uses what it was actually given, is written in the user's voice, and is checked against a
concrete banned-pattern list rather than one more instruction stacked into the prompt.

Two modes (confirmed with the CEO 2026-10-10):
  "direct" - brief -> draft immediately (the earlier prompts-only flow).
  "review" - if the brief is missing something the writer needs, ask clarifying questions
             first instead of drafting; otherwise behaves like "direct"."""

from __future__ import annotations

from gtm_engine.content.models import ContentBrief, ContentDraft, StyleProfile, VerifiedClaim
from gtm_engine.content.patterns import check_ai_patterns
from gtm_engine.content.verify import check_fabricated_numbers
from gtm_engine.llm.client import LLM

LENGTH_HINT: dict[str, str] = {
    "linkedin_post": "150-250 words, line breaks between thoughts, no hashtag spam",
    "linkedin_comment": "1-3 sentences, a direct reply to the post's point",
    "blog": "400-700 words, short paragraphs, no filler intro",
}

_BANNED_PHRASES = (
    "in today's fast-paced world", "let's dive in", "unlock the power", "game changer",
    "it's important to note", "in conclusion", "at the end of the day",
    "navigate the landscape", "in this day and age", "buckle up", "picture this",
    "whether you're a", "the world of", "look no further",
)


def clarifying_questions(brief: ContentBrief) -> list[str]:
    questions: list[str] = []
    if not brief.topic.strip():
        questions.append("What's this piece actually about?")
    if not brief.audience.strip():
        questions.append("Who's it for?")
    if not brief.source_material and not brief.notes.strip():
        questions.append("Any source material, data, or notes I should ground this in?")
    return questions


def _build_system_prompt(brief: ContentBrief, style: StyleProfile,
                          verified: list[VerifiedClaim]) -> str:
    verified_lines = "\n".join(
        f"- {c.claim} (source: {c.source_url})" for c in verified if c.status == "verified"
    )
    unverified_lines = "\n".join(f"- {c.claim}" for c in verified if c.status == "unverified")
    banned = "\n".join(f"- {p}" for p in _BANNED_PHRASES)
    return (
        f"Write a {brief.content_type.replace('_', ' ')}. "
        f"Length: {LENGTH_HINT.get(brief.content_type, '150-250 words')}.\n\n"
        "CONTEXT (use only this - never invent facts, names, or numbers beyond it):\n"
        f"Topic: {brief.topic}\n"
        f"Audience: {brief.audience or 'general professional audience'}\n"
        f"Notes: {brief.notes}\n"
        f"Source material: {'; '.join(brief.source_material) if brief.source_material else '(none provided)'}\n\n"
        f"VERIFIED FACTS you may state as fact:\n{verified_lines or '(none)'}\n\n"
        "UNVERIFIED CLAIMS - do not state these as fact. Either omit them or write them as a "
        f"clearly flagged uncertain aside:\n{unverified_lines or '(none)'}\n\n"
        "WRITE IN THIS VOICE:\n"
        f"Tone: {style.tone}\n"
        f"Sentences: {style.sentence_pattern}\n"
        f"Vocabulary: {style.vocabulary}\n"
        f"Rhetorical habits: {style.rhetorical_habits}\n"
        f"Punctuation: {style.punctuation_habits}\n"
        f"Formatting: {style.formatting_habits}\n"
        f"Opening: {style.opener_habits}\n\n"
        f"NEVER use any of these phrases or constructions:\n{banned}\n"
        "Also avoid: the 'it's not X, it's Y' construction, lists of exactly three items, "
        "uniform sentence length, starting consecutive sentences with 'And'.\n\n"
        "Output ONLY the finished piece, no preamble, no markdown headers."
    )


async def write_content(llm: LLM | None, brief: ContentBrief, style: StyleProfile,
                         verified: list[VerifiedClaim], *, mode: str = "direct",
                         max_tokens: int = 700) -> ContentDraft:
    if mode == "review":
        questions = clarifying_questions(brief)
        if questions:
            return ContentDraft(text="", content_type=brief.content_type,
                                 verified_claims=verified, clarifying_questions=questions)

    if llm is None:
        return ContentDraft(
            text="(no LLM configured - cannot draft content without one)",
            content_type=brief.content_type, verified_claims=verified,
        )

    system = _build_system_prompt(brief, style, verified)
    user_input = brief.topic or brief.raw_text
    grounding_text = "\n".join([
        brief.topic, brief.audience, brief.notes, *brief.source_material,
        *(f"{c.claim} {c.source_url or ''}" for c in verified),
    ])
    text = (await llm.complete(system, user_input, max_tokens=max_tokens)).strip()
    violations = check_ai_patterns(text) + check_fabricated_numbers(text, grounding_text)

    if violations:
        # One targeted rewrite pass, told exactly what to fix - cheaper and more reliable
        # than a longer up-front prompt, and catches what the model drifts into anyway.
        fix_notes = "\n".join(f"- {v.pattern}: {v.detail}" for v in violations)
        rewrite_system = system + f"\n\nYour previous draft had these issues - fix them:\n{fix_notes}"
        text2 = (await llm.complete(rewrite_system, user_input, max_tokens=max_tokens)).strip()
        if text2:
            text = text2
            violations = check_ai_patterns(text) + check_fabricated_numbers(text, grounding_text)

    return ContentDraft(
        text=text, content_type=brief.content_type, verified_claims=verified,
        unverified_flagged=[c.claim for c in verified if c.status == "unverified"],
        pattern_violations=violations,
    )
