"""B1 + B2 + B3: every qualified lead that doesn't become outreach_ready carries a specific
skip reason, logged and persisted. Qualified-no-contact leads land in NEEDS_CONTACT so the
operator can see them and manually promote once a contact is added."""

from __future__ import annotations

import pytest

from gtm_engine.config.schema import CampaignConfig, GeographyConfig, RoutingThresholds
from gtm_engine.models import (
    Classification, CompanyType, Contact, EmailStatus, Priority, SequenceStatus,
)
from gtm_engine.scoring.scoring import ScoreBreakdown, is_outreach_ready


def _campaign(**over) -> CampaignConfig:
    base = dict(
        campaign_id="c1", name="n", offer="o",
        min_score=10, routing=RoutingThresholds(qualified=40, high_priority=55, review=20),
        geography=GeographyConfig(countries=["Pakistan"], cities=["Karachi"]),
    )
    base.update(over)
    return CampaignConfig(**base)


def _score(total=70, priority=Priority.HIGH) -> ScoreBreakdown:
    return ScoreBreakdown(review_band=20, rating_score=10, proximity_tier=10, online_gap=15,
                          pain_evidence=15, total=total, reasons=[], priority=priority)


def _cls(ct=CompanyType.BUYER) -> Classification:
    return Classification(company_type=ct, buyer_fit=70, reasons=[])


# --- is_outreach_ready returns a reason, not just a bool ------------------------------


def test_outreach_ready_when_all_gates_pass():
    campaign = _campaign()
    contact = Contact(email="info@x.pk", email_status=EmailStatus.MX_VALID, phone="+92-300-1")
    ready, reason = is_outreach_ready(_cls(), _score(), contact, campaign)
    assert ready and reason is None


def test_skip_reason_not_a_buyer():
    ready, reason = is_outreach_ready(_cls(CompanyType.VENDOR), _score(), Contact(email="a@b.pk", email_status=EmailStatus.MX_VALID), _campaign())
    assert not ready and reason == "not_a_buyer"


def test_skip_reason_score_below_min():
    campaign = _campaign(min_score=100)
    ready, reason = is_outreach_ready(_cls(), _score(total=50), Contact(email="a@b.pk", email_status=EmailStatus.MX_VALID), campaign)
    assert not ready and reason == "score_below_min"


def test_skip_reason_priority_below_qualified():
    ready, reason = is_outreach_ready(_cls(), _score(total=15, priority=Priority.REVIEW), Contact(email="a@b.pk", email_status=EmailStatus.MX_VALID), _campaign())
    assert not ready and reason == "priority_below_qualified"


def test_skip_reason_no_usable_contact_when_no_email_no_phone():
    ready, reason = is_outreach_ready(_cls(), _score(), Contact(email=None, phone=None), _campaign())
    assert not ready and reason == "no_usable_contact"


def test_skip_reason_no_usable_contact_when_email_is_invalid_or_none_status():
    ready, reason = is_outreach_ready(_cls(), _score(), Contact(email="a@b.pk", email_status=EmailStatus.INVALID), _campaign())
    assert not ready and reason == "no_usable_contact"
    ready2, reason2 = is_outreach_ready(_cls(), _score(), Contact(email=None, email_status=EmailStatus.NONE), _campaign())
    assert not ready2 and reason2 == "no_usable_contact"


# --- SequenceStatus.NEEDS_CONTACT enum value exists ----------------------------------


def test_needs_contact_status_exists():
    assert SequenceStatus.NEEDS_CONTACT.value == "needs_contact"


def test_needs_contact_distinct_from_not_queued_and_suppressed():
    assert SequenceStatus.NEEDS_CONTACT is not SequenceStatus.NOT_QUEUED
    assert SequenceStatus.NEEDS_CONTACT is not SequenceStatus.SUPPRESSED


# --- Lead.outreach_skip_reason persists in the model ---------------------------------


def test_lead_carries_outreach_skip_reason():
    from gtm_engine.models import Lead
    l = Lead(campaign_id="c", company_name="X", outreach_skip_reason="no_usable_contact")
    assert l.outreach_skip_reason == "no_usable_contact"
    # And it round-trips through model_dump (used for DB storage + CSV).
    d = l.model_dump(mode="json")
    assert d["outreach_skip_reason"] == "no_usable_contact"


# --- CSV columns include it ----------------------------------------------------------


def test_csv_columns_include_outreach_skip_reason():
    from gtm_engine.export.csv_export import CLEAN_COLUMNS
    from gtm_engine.models import CSV_COLUMNS
    assert "outreach_skip_reason" in CSV_COLUMNS
    assert "Outreach skip reason" in CLEAN_COLUMNS
