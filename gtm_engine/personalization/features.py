"""Feature extraction for the per-user re-rank bandit (docs/PERSONALIZATION_PLAN.md).

Zero new enrichment cost: every feature here is already computed by score_lead() and sitting
on the Lead by the time personalization runs. The name list is versioned/stable so stored
feature vectors in lead_feedback stay interpretable across releases - extend by appending, never
by reordering or removing."""

from __future__ import annotations

from gtm_engine.models import Lead, ScoreBreakdown

# Order matters - this is the contract with every stored feature_vector row. Append only.
FEATURE_NAMES: list[str] = [
    "bias",
    "review_band", "rating_score", "proximity_tier", "online_gap", "pain_evidence",
    "contact_email_only", "contact_phone_only", "contact_both", "contact_none",
]

# Max points per scoring dimension (scoring/scoring.py), used to normalize into [0, 1].
_MAX = {"review_band": 30, "rating_score": 10, "proximity_tier": 15, "online_gap": 25, "pain_evidence": 20}


def _contact_onehot(has_email: bool, has_phone: bool) -> tuple[float, float, float, float]:
    if has_email and has_phone:
        return 0.0, 0.0, 1.0, 0.0
    if has_email:
        return 1.0, 0.0, 0.0, 0.0
    if has_phone:
        return 0.0, 1.0, 0.0, 0.0
    return 0.0, 0.0, 0.0, 1.0


def extract_features_from_score(score: ScoreBreakdown, has_email: bool, has_phone: bool) -> list[float]:
    """Build the feature vector directly from a freshly computed ScoreBreakdown (used in the
    pipeline, before the Lead is persisted)."""
    email_only, phone_only, both, none_ = _contact_onehot(has_email, has_phone)
    return [
        1.0,
        score.review_band / _MAX["review_band"],
        score.rating_score / _MAX["rating_score"],
        score.proximity_tier / _MAX["proximity_tier"],
        score.online_gap / _MAX["online_gap"],
        score.pain_evidence / _MAX["pain_evidence"],
        email_only, phone_only, both, none_,
    ]


def extract_features(lead: Lead) -> list[float]:
    """Reconstruct the same feature vector from a persisted Lead (used by the feedback
    endpoint, where only the stored lead - not the live scoring inputs - is available)."""
    score = (lead.evidence or {}).get("score") or {}
    email_only, phone_only, both, none_ = _contact_onehot(bool(lead.contact_email), bool(lead.phone))
    return [
        1.0,
        score.get("review_band", 0) / _MAX["review_band"],
        score.get("rating_score", 0) / _MAX["rating_score"],
        score.get("proximity_tier", 0) / _MAX["proximity_tier"],
        score.get("online_gap", 0) / _MAX["online_gap"],
        score.get("pain_evidence", 0) / _MAX["pain_evidence"],
        email_only, phone_only, both, none_,
    ]
