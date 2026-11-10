"""Fact verification (CEO content-quality brief, 2026-10-10): before writing, pull out the
specific, checkable factual claims a brief implies, then check each against a real web
search - reusing the same search_web primitive the discovery pipeline already relies on, no
new dependency or scraping. A claim is verified only when a result's title shares real
content with it; anything else is marked unverified, and the writer (gtm_engine/content/
writer.py) must drop or flag it, never assert it as fact."""

from __future__ import annotations

import json
import logging
import re

from gtm_engine.config.schema import EngineSettings
from gtm_engine.content.models import PatternViolation, VerifiedClaim
from gtm_engine.discovery.search import search_web
from gtm_engine.llm.client import LLM
from gtm_engine.scraping.fetcher import HttpFetcher

log = logging.getLogger(__name__)

_STOPWORDS = {
    "the", "a", "an", "of", "in", "on", "at", "to", "for", "and", "or", "is", "was", "are",
    "were", "with", "by", "its", "it's", "that", "this", "has", "have", "had", "be",
}
_WORD_RE = re.compile(r"[A-Za-z0-9']+")
_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)
_NUMBER_RE = re.compile(r"\d[\d,.]*\s?%|\d[\d,.]*(?:\s?(?:percent|x))\b", re.IGNORECASE)


def _significant_words(text: str) -> set[str]:
    return {w for w in (m.lower() for m in _WORD_RE.findall(text)) if len(w) > 2 and w not in _STOPWORDS}


async def extract_claims(llm: LLM | None, topic: str, notes: str) -> list[str]:
    """The specific, checkable claims (names, numbers, dates, named events/companies/products)
    implied by a brief - not opinions or general statements. Deterministic fallback: none,
    which means without an LLM the writer treats the brief as narrative/opinion only and
    verifies nothing (safer than guessing at claims from raw text)."""
    if llm is None or not topic.strip():
        return []
    system = (
        "Given a content topic and notes, list only the SPECIFIC, CHECKABLE factual claims "
        "implied - names, numbers, dates, named events, named companies or products - not "
        "opinions or general statements. Output a JSON array of short claim strings, max 6. "
        "If there are no checkable claims, output []."
    )
    try:
        raw = await llm.complete(system, f"Topic: {topic}\nNotes: {notes}", max_tokens=300)
    except Exception:
        return []
    match = _JSON_ARRAY_RE.search(raw or "")
    if not match:
        return []
    try:
        arr = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    if not isinstance(arr, list):
        return []
    return [c.strip() for c in arr if isinstance(c, str) and c.strip()][:6]


def _best_match(claim: str, results: list[tuple[str, str]]) -> tuple[str, str] | None:
    sig = _significant_words(claim)
    if not sig:
        return None
    for url, title in results:
        overlap = len(sig & _significant_words(title)) / len(sig)
        if overlap >= 0.5:
            return url, title
    return None


async def verify_claims(fetcher: HttpFetcher, settings: EngineSettings,
                         claims: list[str]) -> list[VerifiedClaim]:
    out: list[VerifiedClaim] = []
    for claim in claims:
        try:
            results = await search_web(fetcher, settings, claim)
        except Exception as exc:
            log.debug("claim verification search failed for %r: %s", claim, exc)
            results = []
        match = _best_match(claim, results)
        if match:
            out.append(VerifiedClaim(claim=claim, status="verified", source_url=match[0]))
        else:
            out.append(VerifiedClaim(claim=claim, status="unverified"))
    return out


def check_fabricated_numbers(draft_text: str, grounding_text: str) -> list[PatternViolation]:
    """Catches invented statistics the model adds while writing (not from the brief, not from
    a verified claim) - the gap plain claim verification misses, since that only checks claims
    extracted from the brief *before* drafting, never what the model makes up *during*
    drafting. A number/percentage in the draft is flagged unless it also appears, verbatim
    modulo spacing, in the grounding text (brief topic+notes+source material+verified claims)."""
    if not draft_text or not draft_text.strip():
        return []
    ground_norm = re.sub(r"\s+", "", grounding_text.lower())
    violations: list[PatternViolation] = []
    seen: set[str] = set()
    for match in _NUMBER_RE.finditer(draft_text):
        token = match.group(0)
        norm = re.sub(r"\s+", "", token.lower())
        if norm in seen:
            continue
        if norm not in ground_norm:
            seen.add(norm)
            violations.append(PatternViolation("fabricated_number", token.strip()))
    return violations
