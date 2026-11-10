"""Content-writing models (CEO content-quality brief, 2026-10-10): a context brief, a
per-user style profile, verified claims, and the resulting draft with any AI-pattern
violations found. See gtm_engine/content/writer.py for how these fit together."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ContentBrief:
    content_type: str = "linkedin_post"  # linkedin_post | linkedin_comment | blog
    topic: str = ""
    audience: str = ""
    source_material: list[str] = field(default_factory=list)
    notes: str = ""
    raw_text: str = ""


@dataclass
class StyleProfile:
    """Defaults describe a plain, non-AI-sounding voice - the fallback when no samples or
    no LLM are available, not a guess at the user's actual style."""

    tone: str = "plain, direct"
    sentence_pattern: str = "mixed length, no uniform rhythm"
    vocabulary: str = "plain English, no jargon unless the topic demands it"
    rhetorical_habits: str = "states the point, does not build to it"
    punctuation_habits: str = "normal punctuation, no stylised em-dash use"
    formatting_habits: str = "plain paragraphs, no emoji bullets unless the source used them"
    opener_habits: str = "starts with the point or a concrete detail, not a hook question"
    sample_count: int = 0


@dataclass
class VerifiedClaim:
    claim: str
    status: str  # "verified" | "unverified"
    source_url: str | None = None


@dataclass
class PatternViolation:
    pattern: str
    detail: str


@dataclass
class ContentDraft:
    text: str
    content_type: str
    verified_claims: list[VerifiedClaim] = field(default_factory=list)
    unverified_flagged: list[str] = field(default_factory=list)
    pattern_violations: list[PatternViolation] = field(default_factory=list)
    clarifying_questions: list[str] = field(default_factory=list)
