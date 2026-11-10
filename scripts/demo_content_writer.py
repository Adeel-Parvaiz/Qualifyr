"""One-off manual demo for the CEO content-quality brief (2026-10-10): runs the real
write_content() pipeline end-to-end (brief -> style profile -> claim verification -> draft ->
AI-pattern check/rewrite) for a LinkedIn post, a LinkedIn comment, and a short blog, using the
live Groq LLM. Not a test - prints output for human review. Delete once reviewed, or keep as a
manual smoke-check; either is fine."""

from __future__ import annotations

import asyncio

from gtm_engine.config.schema import EngineSettings
from gtm_engine.content.brief import parse_content_brief, refine_content_brief
from gtm_engine.content.style import build_style_profile
from gtm_engine.content.verify import extract_claims, verify_claims
from gtm_engine.content.writer import write_content
from gtm_engine.llm.client import build_llm
from gtm_engine.scraping.fetcher import HttpFetcher

STYLE_SAMPLES = [
    "Shipped the onboarding rewrite today. Took three weeks longer than planned because the "
    "edge cases around partial signups kept multiplying - not glamorous work, but support "
    "tickets about confused new users dropped hard on day one.",
    "Most sales tools sell you more leads. We sell you fewer, better ones. Our last campaign "
    "returned 4 qualified leads out of 90 scanned businesses - all 4 converted to demos. "
    "That ratio is the whole product.",
]

CASES = [
    {
        "label": "LinkedIn post",
        "prompt": (
            "write a linkedin post about how Qualifyr found 4 qualified leads out of 90 "
            "grocery stores scanned in Islamabad last week, all 4 had no working website or "
            "online ordering, and all 4 converted to a demo call"
        ),
    },
    {
        "label": "LinkedIn comment",
        "prompt": (
            "write a comment replying to this post about a founder complaining that most "
            "lead-gen tools just dump a list of 500 unqualified contacts on you"
        ),
    },
    {
        "label": "Short blog",
        "prompt": (
            "write a blog article about why Qualifyr only returns 3-5 leads per campaign "
            "instead of hundreds, and why that's the point, not a limitation"
        ),
    },
]


async def main() -> None:
    llm = build_llm()
    if llm is None:
        print("No LLM configured (checked Ollama/Groq/Gemini) - aborting demo.")
        return
    print(f"Using LLM provider: {llm.name}\n")

    settings = EngineSettings()
    style = await build_style_profile(llm, STYLE_SAMPLES)
    print("--- Style profile built from 2 samples ---")
    print(style)
    print()

    async with HttpFetcher(settings) as fetcher:
        for case in CASES:
            print(f"{'=' * 70}\n{case['label'].upper()}\n{'=' * 70}")
            brief = parse_content_brief(case["prompt"])
            brief = await refine_content_brief(llm, brief)
            print(f"Brief: type={brief.content_type!r} topic={brief.topic!r} "
                  f"audience={brief.audience!r}")

            claims = await extract_claims(llm, brief.topic, brief.notes)
            verified = await verify_claims(fetcher, settings, claims) if claims else []
            if verified:
                print(f"Claims checked: {[(c.claim, c.status) for c in verified]}")

            draft = await write_content(llm, brief, style, verified, mode="direct")
            print(f"\n--- DRAFT ({len(draft.text.split())} words) ---")
            print(draft.text)
            if draft.pattern_violations:
                print(f"\n[AI-pattern violations remaining after rewrite pass: "
                      f"{[v.pattern for v in draft.pattern_violations]}]")
            else:
                print("\n[No AI-pattern violations after generation/rewrite pass.]")
            if draft.unverified_flagged:
                print(f"[Unverified claims flagged, not asserted as fact: {draft.unverified_flagged}]")
            print()


if __name__ == "__main__":
    asyncio.run(main())
