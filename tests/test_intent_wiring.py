"""Slice 2: the intent verdict drives the buyer/unknown call and the score.

apply_intent_verdict() folds an LLM verdict into a Classification; score_lead() then rewards an
evident need. These are pure and offline (no DB, no live LLM)."""

import pytest

from gtm_engine.config.schema import CampaignConfig, GeographyConfig
from gtm_engine.models import (
    Classification, CompanyQuality, CompanyType, Contact, DiscoveredCompany, EmailStatus, Signals,
)
from gtm_engine.pipeline import apply_intent_verdict
from gtm_engine.scoring.scoring import ScoreInputs, score_lead


def _cls(ctype, confidence=0.5, buyer_hits=None):
    return Classification(company_type=ctype, confidence=confidence, buyer_hits=buyer_hits or [])


def V(buyer, confidence, reason="grounded reason"):
    return {"buyer": buyer, "confidence": confidence, "reason": reason, "by": "llm:fake"}


# --- apply_intent_verdict: type promotion / demotion --------------------------------------

def test_confident_buyer_promotes_unknown_to_buyer():
    cls = _cls(CompanyType.UNKNOWN, 0.3)
    note = apply_intent_verdict(cls, V(True, 0.9))
    assert cls.company_type == CompanyType.BUYER
    assert cls.intent_buyer is True and cls.intent_confidence == 0.9
    assert cls.confidence >= 0.9 and "buyer" in note


def test_confident_non_buyer_demotes_keyword_only_buyer_to_unknown():
    cls = _cls(CompanyType.BUYER, 0.5, buyer_hits=["retail"])
    apply_intent_verdict(cls, V(False, 0.85, "an agency that sells services"))
    assert cls.company_type == CompanyType.UNKNOWN
    assert cls.intent_buyer is False
    assert any("no evident need" in r for r in cls.reasons)


def test_weak_verdict_is_recorded_but_does_not_flip_the_type():
    cls = _cls(CompanyType.BUYER, 0.5)
    apply_intent_verdict(cls, V(False, 0.4))       # below the 0.6 threshold
    assert cls.company_type == CompanyType.BUYER   # unchanged
    assert cls.intent_buyer is False and cls.intent_confidence == 0.4

    cls2 = _cls(CompanyType.UNKNOWN, 0.2)
    apply_intent_verdict(cls2, V(True, 0.4))
    assert cls2.company_type == CompanyType.UNKNOWN


def test_a_confident_buyer_verdict_does_not_downgrade_an_existing_buyer():
    cls = _cls(CompanyType.BUYER, 0.7)
    apply_intent_verdict(cls, V(True, 0.95))
    assert cls.company_type == CompanyType.BUYER


# --- scoring: intent moves buyer evidence -------------------------------------------------

def _campaign():
    return CampaignConfig(campaign_id="c", name="C", offer="inventory software",
                          geography=GeographyConfig(countries=["Pakistan"], cities=["Lahore"]),
                          buyer_keywords=["retailer"], min_score=70)


def _score(cls, signals=None):
    company = DiscoveredCompany(name="Co", source="osm", country="Pakistan", city="Lahore")
    return score_lead(ScoreInputs(company, cls, CompanyQuality(reachable=True), Contact(), signals or Signals()), _campaign())


def test_intent_match_raises_the_score_over_an_unjudged_buyer():
    base = _cls(CompanyType.BUYER, 0.6)
    judged = _cls(CompanyType.BUYER, 0.6)
    apply_intent_verdict(judged, V(True, 0.95, "runs 40 outlets, manual stock"))
    # Intent classification surfaces in reasons; actual score boost requires intent signals.
    intent_signals = Signals(intent=[{"kind": "tender", "text": "inventory management RFQ"}])
    assert _score(judged, intent_signals).total > _score(base).total
    assert any("intent match" in r for r in _score(judged).reasons)


def test_intent_non_buyer_reason_is_surfaced_in_the_score():
    cls = _cls(CompanyType.UNKNOWN, 0.3)
    apply_intent_verdict(cls, V(False, 0.8, "a marketing agency"))
    assert any("no evident need" in r for r in _score(cls).reasons)
