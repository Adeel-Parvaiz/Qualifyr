import pytest

from gtm_engine.config.schema import CampaignConfig, RoutingThresholds
from gtm_engine.models import CompanyType, EmailStatus, Lead, Priority
from gtm_engine.scoring.requalification import recheck_qualification


def campaign():
    return CampaignConfig(campaign_id="camp", name="Retail", offer="Inventory software", min_score=25,
                          routing=RoutingThresholds(high_priority=55, qualified=25, review=20))


def buyer(**changes):
    values = dict(campaign_id="camp", company_name="Reviewed retailer", company_type=CompanyType.BUYER,
                  total_score=36, priority=Priority.REVIEW, contact_email="test@example.com",
                  email_status=EmailStatus.MX_VALID, outreach_skip_reason="score_below_min")
    values.update(changes)
    return Lead(**values)


def test_recheck_uses_existing_score_and_new_thresholds():
    old = buyer()
    result = recheck_qualification(old, campaign())
    assert result.outreach_ready
    assert result.priority == Priority.QUALIFIED
    assert result.outreach_skip_reason is None
    assert result.total_score == old.total_score == 36
    assert not old.outreach_ready


@pytest.mark.parametrize("changes,reason", [
    ({"total_score": 24}, "score_below_min"),
    ({"company_type": CompanyType.UNKNOWN}, "not_a_buyer"),
    ({"company_type": CompanyType.VENDOR}, "not_a_buyer"),
    ({"contact_email": None, "phone": None}, "no_usable_contact"),
    ({"evidence": {"quality": {"website_mismatch": True}}}, "priority_below_qualified"),
    ({"review_verdict": "wrong_email"}, "review_required"),
])
def test_recheck_preserves_qualification_blocks(changes, reason):
    result = recheck_qualification(buyer(**changes), campaign())
    assert not result.outreach_ready
    assert result.outreach_skip_reason == reason


def test_recheck_does_not_promote_above_qualified_priority_gate():
    cfg = campaign()
    cfg.routing.qualified = 40
    result = recheck_qualification(buyer(), cfg)
    assert not result.outreach_ready
    assert result.outreach_skip_reason == "priority_below_qualified"


def test_recheck_route_keeps_suppressed_buyers_out(monkeypatch):
    from unittest.mock import Mock
    from gtm_engine.api import main
    db = Mock()
    db.get_run_progress.return_value = None
    db.list_leads.return_value = [buyer()]
    db.is_suppressed.return_value = True
    monkeypatch.setattr(main, "_db", lambda: db)
    monkeypatch.setattr(main, "_campaign", lambda _: campaign())
    result = main.requalify_campaign("camp")
    assert result["email_ready"] == result["outreach_ready"] == 0
    updated = db.update_lead.call_args.args[0]
    assert not updated.outreach_ready
    assert updated.outreach_skip_reason == "suppressed"
