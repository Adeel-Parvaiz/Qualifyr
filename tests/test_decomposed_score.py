"""Tests for the decomposed transparent scoring system.

Reference formula: review_band(0-30) + rating(0-10) + proximity_tier(0-15)
                   + online_gap(0-25) + pain_evidence(0-20) = max 100
"""

import pytest

from gtm_engine.config.schema import CampaignConfig, EngineSettings, GeographyConfig
from gtm_engine.models import (
    Classification, CompanyQuality, CompanyType, Contact, DiscoveredCompany,
    EmailStatus, OnlinePresence, Priority, Signals,
)
from gtm_engine.scoring.proximity import TIER_POINTS, haversine_km, proximity_tier
from gtm_engine.scoring.scoring import ScoreInputs, is_outreach_ready, score_lead


# ── helpers ──────────────────────────────────────────────────────────────

def _campaign(**kw) -> CampaignConfig:
    defaults = dict(
        campaign_id="test", name="test", offer="inventory software",
        geography=GeographyConfig(countries=["Pakistan"], cities=["Islamabad"]),
        buyer_keywords=["store"],
    )
    defaults.update(kw)
    return CampaignConfig(**defaults)


def _company(**kw) -> DiscoveredCompany:
    base = dict(name="Test Store", website="https://example.pk", city="Islamabad",
                country="Pakistan", source="osm")
    base.update(kw)
    return DiscoveredCompany(**base)


def _base_inputs(**kw) -> ScoreInputs:
    defaults = dict(
        company=_company(),
        classification=Classification(company_type=CompanyType.BUYER, confidence=0.9, buyer_hits=["store"]),
        quality=CompanyQuality(reachable=True, https=True),
        contact=Contact(name="Owner", role="CEO", email="x@example.pk",
                        email_status=EmailStatus.MX_VALID, is_decision_maker=True),
        signals=Signals(),
    )
    defaults.update(kw)
    return ScoreInputs(**defaults)


# ── haversine ──────────────────────────────────────────────────────────

def test_haversine_same_point():
    assert haversine_km(33.7, 72.97, 33.7, 72.97) == 0.0


def test_haversine_known_distance():
    d = haversine_km(33.6975, 72.9730, 33.6874, 72.9730)
    assert 1.0 < d < 1.5


# ── proximity_tier ─────────────────────────────────────────────────────

def test_tier1_close():
    tier, dist = proximity_tier(33.70, 72.97, 33.70, 72.98)
    assert tier == 1 and dist is not None and dist < 5.0


def test_tier2_mid():
    tier, dist = proximity_tier(33.80, 72.97, 33.70, 72.97)
    assert tier == 2 and 5.0 < dist < 15.0


def test_tier3_far():
    tier, dist = proximity_tier(34.05, 72.97, 33.70, 72.97)
    assert tier == 3 and dist > 15.0


def test_no_anchor_defaults_tier1():
    tier, dist = proximity_tier(33.70, 72.97, None, None)
    assert tier == 1 and dist is None


def test_no_company_coords_defaults_tier1():
    tier, dist = proximity_tier(None, None, 33.70, 72.97)
    assert tier == 1 and dist is None


def test_tier_points():
    assert TIER_POINTS[1] == 15
    assert TIER_POINTS[2] == 12
    assert TIER_POINTS[3] == 8


# ── review band scoring ───────────────────────────────────────────────

def test_review_band_200_plus():
    op = OnlinePresence(google_review_count=250, online_gap_score=0)
    score = score_lead(_base_inputs(online_presence=op), _campaign())
    assert score.review_band == 30


def test_review_band_100_199():
    op = OnlinePresence(google_review_count=150, online_gap_score=0)
    score = score_lead(_base_inputs(online_presence=op), _campaign())
    assert score.review_band == 24


def test_review_band_50_99():
    op = OnlinePresence(google_review_count=75, online_gap_score=0)
    score = score_lead(_base_inputs(online_presence=op), _campaign())
    assert score.review_band == 18


def test_review_band_10_49():
    op = OnlinePresence(google_review_count=25, online_gap_score=0)
    score = score_lead(_base_inputs(online_presence=op), _campaign())
    assert score.review_band == 12


def test_review_band_1_9():
    op = OnlinePresence(google_review_count=5, online_gap_score=0)
    score = score_lead(_base_inputs(online_presence=op), _campaign())
    assert score.review_band == 6


def test_review_band_none():
    score = score_lead(_base_inputs(), _campaign())
    assert score.review_band == 0


# ── rating scoring ────────────────────────────────────────────────────

def test_rating_5_star():
    op = OnlinePresence(google_rating=5.0, online_gap_score=0)
    score = score_lead(_base_inputs(online_presence=op), _campaign())
    assert score.rating_score == 10


def test_rating_3_star():
    op = OnlinePresence(google_rating=3.0, online_gap_score=0)
    score = score_lead(_base_inputs(online_presence=op), _campaign())
    assert score.rating_score == 5


def test_rating_1_star():
    op = OnlinePresence(google_rating=1.0, online_gap_score=0)
    score = score_lead(_base_inputs(online_presence=op), _campaign())
    assert score.rating_score == 0


def test_rating_none():
    score = score_lead(_base_inputs(), _campaign())
    assert score.rating_score == 0


# ── online gap scoring ────────────────────────────────────────────────

def test_high_gap_scores_high():
    op = OnlinePresence(online_gap_score=22)
    score = score_lead(_base_inputs(online_presence=op), _campaign())
    assert score.online_gap == 22


def test_zero_gap_scores_zero():
    op = OnlinePresence(online_gap_score=0)
    score = score_lead(_base_inputs(online_presence=op), _campaign())
    assert score.online_gap == 0


# ── pain evidence scoring ─────────────────────────────────────────────

def test_pain_from_reviews_scores():
    op = OnlinePresence(
        pain_from_reviews=["expired products", "rude staff", "overpriced"],
        online_gap_score=0,
    )
    score = score_lead(_base_inputs(online_presence=op), _campaign())
    assert score.pain_evidence >= 9
    assert any("review pain" in r for r in score.reasons)


def test_pain_from_signals_scores():
    signals = Signals(pain={"customer_service_load": ["WhatsApp orders"]})
    score = score_lead(_base_inputs(signals=signals), _campaign())
    assert score.pain_evidence >= 1


def test_intent_signals_add_pain_evidence():
    signals = Signals(intent=[{"kind": "tender", "text": "ERP system needed", "source": "ppra"}])
    score = score_lead(_base_inputs(signals=signals), _campaign())
    assert score.pain_evidence >= 2


# ── total composition ─────────────────────────────────────────────────

def test_total_equals_sum_of_parts():
    op = OnlinePresence(
        google_review_count=200, google_rating=4.5,
        online_gap_score=20, pain_from_reviews=["rude staff"],
    )
    score = score_lead(_base_inputs(online_presence=op), _campaign())
    assert score.total == (score.review_band + score.rating_score + score.proximity_tier
                           + score.online_gap + score.pain_evidence)


def test_max_score_is_100():
    op = OnlinePresence(
        google_review_count=500, google_rating=5.0,
        online_gap_score=25,
        pain_from_reviews=["a", "b", "c", "d", "e"],
    )
    score = score_lead(_base_inputs(online_presence=op), _campaign())
    assert score.total <= 100


# ── routing ───────────────────────────────────────────────────────────

def test_vendor_always_rejected():
    cls = Classification(company_type=CompanyType.VENDOR, confidence=0.9)
    op = OnlinePresence(google_review_count=500, google_rating=5.0, online_gap_score=25)
    score = score_lead(_base_inputs(classification=cls, online_presence=op), _campaign())
    assert score.priority == Priority.REJECT
    assert "VENDOR" in score.reasons[0]


def test_unknown_held_for_review():
    cls = Classification(company_type=CompanyType.UNKNOWN, confidence=0.5)
    op = OnlinePresence(google_review_count=200, google_rating=4.5, online_gap_score=22)
    score = score_lead(_base_inputs(classification=cls, online_presence=op), _campaign())
    assert score.priority in (Priority.REVIEW, Priority.REJECT)


def test_outreach_ready_requires_buyer_and_min_score():
    campaign = _campaign()
    inputs = _base_inputs(
        online_presence=OnlinePresence(
            google_review_count=200, google_rating=4.5, online_gap_score=22,
            pain_from_reviews=["expired products"],
        )
    )
    score = score_lead(inputs, campaign)
    assert score.total >= campaign.min_score
    assert is_outreach_ready(inputs.classification, score, inputs.contact, campaign)[0]


# ── proximity in score ────────────────────────────────────────────────

def test_proximity_with_anchor():
    settings = EngineSettings(anchor_lat=33.70, anchor_lon=72.97)
    company = _company(extra={"lat": 33.70, "lon": 72.98})
    score = score_lead(_base_inputs(company=company, settings=settings), _campaign())
    assert score.proximity_tier == 15


def test_proximity_far_from_anchor():
    settings = EngineSettings(anchor_lat=33.70, anchor_lon=72.97)
    company = _company(extra={"lat": 34.10, "lon": 72.97})
    score = score_lead(_base_inputs(company=company, settings=settings), _campaign())
    assert score.proximity_tier == 8


def test_no_anchor_defaults_full_points():
    score = score_lead(_base_inputs(), _campaign())
    assert score.proximity_tier == 15
