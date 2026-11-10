"""Tests for review text pain mining (deterministic fallback + LLM task)."""

import pytest

from gtm_engine.llm.tasks import _fallback_review_pain, extract_review_pain


# ── deterministic fallback ─────────────────────────────────────────────

def test_fallback_detects_expired():
    reviews = ["They sold me an expired drink! The date was 3 months past."]
    pains = _fallback_review_pain(reviews)
    assert any("expired" in p for p in pains)


def test_fallback_detects_rude_staff():
    reviews = ["The manager was very rude and arrogant when I asked for a refund."]
    pains = _fallback_review_pain(reviews)
    assert any("rude" in p for p in pains)


def test_fallback_detects_stockouts():
    reviews = ["Half the shelves were empty. They're always out of stock on basic items."]
    pains = _fallback_review_pain(reviews)
    assert any("stockout" in p for p in pains)


def test_fallback_detects_hygiene():
    reviews = ["The store was dirty and I saw cockroaches near the produce section."]
    pains = _fallback_review_pain(reviews)
    assert any("hygiene" in p or "cleanliness" in p for p in pains)


def test_fallback_detects_overpricing():
    reviews = ["Everything is overpriced compared to other stores in the area."]
    pains = _fallback_review_pain(reviews)
    assert any("overpriced" in p or "price" in p for p in pains)


def test_fallback_detects_no_online():
    reviews = ["There's no website or app to order from. You have to go physically."]
    pains = _fallback_review_pain(reviews)
    assert any("no online" in p or "delivery" in p for p in pains)


def test_fallback_multiple_pains():
    reviews = [
        "Rude staff, items out of stock, and the place was filthy.",
        "They substituted my item with something cheaper without asking.",
    ]
    pains = _fallback_review_pain(reviews)
    assert len(pains) >= 3


def test_fallback_no_pain_in_positive_reviews():
    reviews = ["Great store! Friendly staff, everything well stocked. Highly recommend."]
    pains = _fallback_review_pain(reviews)
    assert len(pains) == 0


def test_fallback_empty_input():
    assert _fallback_review_pain([]) == []


# ── async extract (LLM=None → fallback) ────────────────────────────────

@pytest.mark.asyncio
async def test_extract_no_llm_uses_fallback():
    reviews = ["Expired milk on the shelf, very disappointing."]
    pains = await extract_review_pain(None, "Test Store", reviews)
    assert any("expired" in p for p in pains)


@pytest.mark.asyncio
async def test_extract_empty_reviews():
    pains = await extract_review_pain(None, "Test Store", [])
    assert pains == []


# ── gap label integration ──────────────────────────────────────────────

def test_review_complaints_gap_label():
    from gtm_engine.enrichment.online_presence import online_gap_labels
    from gtm_engine.models import OnlinePresence

    op = OnlinePresence(pain_from_reviews=["expired products", "rude staff"])
    labels = online_gap_labels(op)
    assert "review_complaints" in labels


def test_no_review_complaints_gap_label():
    from gtm_engine.enrichment.online_presence import online_gap_labels
    from gtm_engine.models import OnlinePresence

    op = OnlinePresence(pain_from_reviews=[])
    labels = online_gap_labels(op)
    assert "review_complaints" not in labels


# ── pitch angle picks up review complaints ─────────────────────────────

@pytest.mark.asyncio
async def test_pitch_includes_review_complaints():
    from gtm_engine.llm.tasks import generate_pitch_angle
    pitch = await generate_pitch_angle(
        None, "POS system", "Bad Store",
        online_gaps=["review_complaints"],
        pain_signals=[], buying_signals=[],
    )
    assert "Bad Store" in pitch
    assert "complaint" in pitch.lower() or "review" in pitch.lower()
