"""Reapply campaign routing to saved evidence without another discovery run."""

from gtm_engine.config.schema import CampaignConfig
from gtm_engine.models import Classification, CompanyType, Contact, Lead, Priority, ScoreBreakdown
from gtm_engine.scoring.scoring import is_outreach_ready


def recheck_qualification(lead: Lead, campaign: CampaignConfig) -> Lead:
    result = lead.model_copy(deep=True)
    thresholds = campaign.routing
    if result.company_type == CompanyType.VENDOR:
        priority = Priority.REJECT
    elif result.total_score >= thresholds.high_priority:
        priority = Priority.HIGH
    elif result.total_score >= thresholds.qualified:
        priority = Priority.QUALIFIED
    elif result.total_score >= thresholds.review:
        priority = Priority.REVIEW
    else:
        priority = Priority.REJECT
    quality = result.evidence.get("quality") or {}
    if priority in (Priority.HIGH, Priority.QUALIFIED) and (
        result.company_type == CompanyType.UNKNOWN or quality.get("website_mismatch")
        or "website may belong to a different company" in result.score_reason
    ):
        priority = Priority.REVIEW
    result.priority = priority
    result.outreach_ready, result.outreach_skip_reason = is_outreach_ready(
        Classification(company_type=result.company_type),
        ScoreBreakdown(total=result.total_score, priority=priority),
        Contact(email=result.contact_email, email_status=result.email_status, phone=result.phone),
        campaign,
    )
    if result.review_verdict and result.review_verdict.startswith("wrong_"):
        result.outreach_ready = False
        result.outreach_skip_reason = "review_required"
    return result
