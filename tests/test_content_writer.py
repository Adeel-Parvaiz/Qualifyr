"""Content-writing feature (CEO content-quality brief, 2026-10-10): context brief, style
profile, fact verification, and the AI-pattern checker. Offline - search_web is
monkeypatched, no network and no DB."""

import json

import pytest

from gtm_engine.content import verify as verify_mod
from gtm_engine.content.brief import parse_content_brief, refine_content_brief
from gtm_engine.content.models import ContentBrief, StyleProfile, VerifiedClaim
from gtm_engine.content.patterns import check_ai_patterns
from gtm_engine.content.style import build_style_profile
from gtm_engine.content.verify import check_fabricated_numbers, extract_claims, verify_claims
from gtm_engine.content.writer import clarifying_questions, write_content


class FakeLLM:
    name = "fake"

    def __init__(self, reply: str):
        self.reply = reply

    async def complete(self, system, user, *, max_tokens=400):
        return self.reply


# --- patterns.py: deterministic AI-pattern checker --------------------------------------

def test_clean_human_sounding_text_has_no_violations():
    text = (
        "Shipped the new onboarding flow today. Took three weeks, mostly because the edge "
        "cases around partial signups kept multiplying. Worth it though - support tickets "
        "about confused new users dropped fast in the first day of testing."
    )
    assert check_ai_patterns(text) == []


def test_detects_cliche_phrase():
    v = check_ai_patterns("In today's fast-paced world, teams need to move quickly.")
    assert any(p.pattern == "cliche_phrase" for p in v)


def test_detects_not_x_but_y_construction():
    v = check_ai_patterns("It's not just a tool, it's a whole new way of working for teams everywhere.")
    assert any(p.pattern == "not_x_but_y" for p in v)


def test_detects_exactly_three_item_list():
    text = "Here is what matters:\n- speed\n- clarity\n- trust\n\nThat's the whole pitch."
    v = check_ai_patterns(text)
    assert any(p.pattern == "three_item_list" for p in v)


def test_four_item_list_is_not_flagged():
    text = "Here is what matters:\n- speed\n- clarity\n- trust\n- cost\n\nThat's the pitch."
    v = check_ai_patterns(text)
    assert not any(p.pattern == "three_item_list" for p in v)


def test_detects_overused_and_sentence_starts():
    text = (
        "We shipped the feature. And it broke in staging. And we fixed it by noon. "
        "And the team moved on to the next thing. And nobody noticed by Friday."
    )
    v = check_ai_patterns(text)
    assert any(p.pattern == "overused_and" for p in v)


def test_detects_uniform_sentence_length():
    sentence = "The team shipped the feature on time today."  # 8 words
    text = " ".join([sentence] * 6)
    v = check_ai_patterns(text)
    assert any(p.pattern == "uniform_sentence_length" for p in v)


def test_empty_text_has_no_violations():
    assert check_ai_patterns("") == []
    assert check_ai_patterns("   ") == []


# --- brief.py: lay prompt -> ContentBrief -------------------------------------------------

def test_parse_content_brief_detects_type_from_keywords():
    assert parse_content_brief("write a comment replying to this post about hiring").content_type == "linkedin_comment"
    assert parse_content_brief("write a blog article about remote work").content_type == "blog"
    assert parse_content_brief("write a linkedin post about our launch").content_type == "linkedin_post"
    assert parse_content_brief("something with no hints at all").content_type == "linkedin_post"


def test_parse_content_brief_deterministic_stage_never_invents():
    brief = parse_content_brief("our new pricing page launched today")
    assert brief.topic == "our new pricing page launched today"
    assert brief.notes == brief.topic
    assert brief.audience == ""  # nothing invented beyond the raw text


async def test_refine_content_brief_without_llm_returns_brief_unchanged():
    brief = parse_content_brief("write a post about our launch")
    out = await refine_content_brief(None, brief)
    assert out is brief


async def test_refine_content_brief_merges_llm_fields():
    reply = json.dumps({
        "content_type": "blog",
        "topic": "Launching our new pricing page",
        "audience": "SaaS founders",
        "notes": "Price dropped 20%, launched Monday",
    })
    brief = parse_content_brief("write something about our launch")
    out = await refine_content_brief(FakeLLM(reply), brief)
    assert out.content_type == "blog"
    assert out.topic == "Launching our new pricing page"
    assert out.audience == "SaaS founders"


async def test_refine_content_brief_ignores_invalid_content_type():
    reply = json.dumps({"content_type": "tweet", "topic": "x"})
    brief = parse_content_brief("write about x")
    out = await refine_content_brief(FakeLLM(reply), brief)
    assert out.content_type == "linkedin_post"  # unchanged - "tweet" isn't a valid type


# --- style.py: forensic style-profile extraction ------------------------------------------

async def test_build_style_profile_no_samples_returns_defaults():
    profile = await build_style_profile(None, [])
    assert profile == StyleProfile()


async def test_build_style_profile_without_llm_keeps_defaults_but_counts_samples():
    profile = await build_style_profile(None, ["sample one", "sample two"])
    assert profile.sample_count == 2
    assert profile.tone == StyleProfile().tone  # default, not invented


async def test_build_style_profile_with_llm_populates_fields():
    reply = json.dumps({
        "tone": "blunt, a little dry",
        "sentence_pattern": "short, clipped",
        "vocabulary": "no jargon",
        "rhetorical_habits": "leads with the conclusion",
        "punctuation_habits": "almost no commas",
        "formatting_habits": "single-line paragraphs",
        "opener_habits": "opens with a flat statement",
    })
    profile = await build_style_profile(FakeLLM(reply), ["sample one", "sample two"])
    assert profile.tone == "blunt, a little dry"
    assert profile.sentence_pattern == "short, clipped"
    assert profile.sample_count == 2


# --- verify.py: claim extraction + search-backed verification ----------------------------

async def test_extract_claims_without_llm_returns_empty():
    assert await extract_claims(None, "our launch", "notes") == []


async def test_extract_claims_parses_llm_json_array():
    claims = await extract_claims(FakeLLM('["Revenue grew 40% in Q3", "Launched in Karachi"]'), "growth", "")
    assert claims == ["Revenue grew 40% in Q3", "Launched in Karachi"]


async def test_verify_claims_marks_matching_result_as_verified(monkeypatch):
    async def fake_search(fetcher, settings, query, **kw):
        return [("https://example.com/a", "Qualifyr launches in Karachi this month")]
    monkeypatch.setattr(verify_mod, "search_web", fake_search)
    result = await verify_claims(None, None, ["Qualifyr launched in Karachi"])
    assert result[0].status == "verified"
    assert result[0].source_url == "https://example.com/a"


async def test_verify_claims_marks_unrelated_result_as_unverified(monkeypatch):
    async def fake_search(fetcher, settings, query, **kw):
        return [("https://example.com/b", "Completely unrelated page about weather")]
    monkeypatch.setattr(verify_mod, "search_web", fake_search)
    result = await verify_claims(None, None, ["Qualifyr launched in Karachi"])
    assert result[0].status == "unverified"
    assert result[0].source_url is None


async def test_verify_claims_marks_no_results_as_unverified(monkeypatch):
    async def fake_search(fetcher, settings, query, **kw):
        return []
    monkeypatch.setattr(verify_mod, "search_web", fake_search)
    result = await verify_claims(None, None, ["A claim nobody can find"])
    assert result[0].status == "unverified"


def test_check_fabricated_numbers_flags_number_not_in_grounding_text():
    v = check_fabricated_numbers("Conversion jumped 70% after the change.", "our launch went well")
    assert any(p.pattern == "fabricated_number" for p in v)


def test_check_fabricated_numbers_allows_number_present_in_grounding_text():
    v = check_fabricated_numbers(
        "Revenue grew 40% in Q3.", "Topic: Revenue grew 40% in Q3 after the relaunch")
    assert v == []


def test_check_fabricated_numbers_empty_text_has_no_violations():
    assert check_fabricated_numbers("", "some grounding text") == []


# --- writer.py: ties context + style + verification + pattern check together -------------

def test_clarifying_questions_flags_missing_topic_audience_and_grounding():
    brief = ContentBrief(topic="", audience="", notes="", source_material=[])
    qs = clarifying_questions(brief)
    assert len(qs) == 3


def test_clarifying_questions_empty_when_brief_is_sufficient():
    brief = ContentBrief(topic="our launch", audience="founders", notes="grew 40%")
    assert clarifying_questions(brief) == []


async def test_review_mode_asks_instead_of_drafting_when_brief_is_thin():
    brief = ContentBrief(topic="", audience="", notes="")
    draft = await write_content(FakeLLM("should not be called"), brief, StyleProfile(), [], mode="review")
    assert draft.text == ""
    assert draft.clarifying_questions


async def test_direct_mode_drafts_even_with_a_thin_brief():
    brief = ContentBrief(topic="", audience="", notes="")
    draft = await write_content(FakeLLM("A short draft."), brief, StyleProfile(), [], mode="direct")
    assert draft.text == "A short draft."


async def test_write_content_without_llm_returns_placeholder_not_invented_text():
    brief = ContentBrief(topic="our launch")
    draft = await write_content(None, brief, StyleProfile(), [], mode="direct")
    assert "no LLM" in draft.text


async def test_write_content_triggers_one_rewrite_pass_on_violation():
    class TwoStepLLM:
        name = "fake"

        def __init__(self):
            self.calls = 0

        async def complete(self, system, user, *, max_tokens=400):
            self.calls += 1
            if self.calls == 1:
                return "In today's fast-paced world, everyone needs this."
            return "Shipped it Monday. Reactions were mixed but mostly positive."

    llm = TwoStepLLM()
    brief = ContentBrief(topic="our launch")
    draft = await write_content(llm, brief, StyleProfile(), [], mode="direct")
    assert llm.calls == 2
    assert draft.text == "Shipped it Monday. Reactions were mixed but mostly positive."
    assert draft.pattern_violations == []


async def test_write_content_triggers_rewrite_on_fabricated_number():
    class TwoStepLLM:
        name = "fake"

        def __init__(self):
            self.calls = 0

        async def complete(self, system, user, *, max_tokens=400):
            self.calls += 1
            if self.calls == 1:
                return "Conversion jumped 70% after the change."
            return "Conversion improved after the change."

    llm = TwoStepLLM()
    brief = ContentBrief(topic="our launch", notes="no specific numbers given")
    draft = await write_content(llm, brief, StyleProfile(), [], mode="direct")
    assert llm.calls == 2
    assert draft.text == "Conversion improved after the change."


async def test_write_content_surfaces_unverified_claims_without_dropping_the_draft():
    brief = ContentBrief(topic="our launch")
    verified = [VerifiedClaim(claim="Revenue grew 40%", status="unverified")]
    draft = await write_content(FakeLLM("A short draft."), brief, StyleProfile(), verified, mode="direct")
    assert draft.unverified_flagged == ["Revenue grew 40%"]
