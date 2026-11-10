"""Intent from the company's own pages: RFQ / tender notices, procurement pages, and job
posts for roles that imply a purchase (hiring a procurement manager or an ERP lead means
budget is moving). Keyword-driven from config/defaults/intent.yaml."""

from __future__ import annotations

import re

from gtm_engine.config.schema import DefaultRules
from gtm_engine.intent.models import IntentSignal
from gtm_engine.scraping.site_crawler import SiteSnapshot


def _hits(text: str, phrases: list[str]) -> list[str]:
    low = text.lower()
    return [p for p in phrases if re.search(r"(?<![a-z])" + re.escape(p.lower()) + r"(?![a-z])", low)]


def _excerpt(text: str, phrase: str, width: int = 160) -> str:
    i = text.lower().find(phrase.lower())
    if i < 0:
        return text[:width]
    start = max(0, i - width // 3)
    return ("…" if start else "") + text[start:start + width].strip() + "…"


def intent_from_pages(snapshot: SiteSnapshot, defaults: DefaultRules) -> list[IntentSignal]:
    out: list[IntentSignal] = []
    for kind, page in snapshot.pages.items():
        text = page.text
        for phrase in _hits(text, defaults.intent_rfq_phrases):
            out.append(IntentSignal(kind="rfq", source="website", source_url=page.url,
                                    text=_excerpt(text, phrase), matched_terms=[phrase]))
        if kind == "careers" or kind == "home":
            for role in _hits(text, defaults.intent_hiring_roles):
                out.append(IntentSignal(kind="hiring", source="website", source_url=page.url,
                                        text=_excerpt(text, role), matched_terms=[role]))
    # one signal per (kind, phrase)
    seen: set[tuple[str, str]] = set()
    unique = []
    for s in out:
        key = (s.kind, s.matched_terms[0] if s.matched_terms else s.text[:30])
        if key not in seen:
            seen.add(key)
            unique.append(s)
    return unique[:6]
