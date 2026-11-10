"""Daily per-user usage limits for metered APIs.

Default limits are conservative for free-tier sustainability.
Override per resource via GTM_DAILY_LIMIT_{RESOURCE} env vars.
"""

from __future__ import annotations

import math
import os

from gtm_engine.pricing.tiers import (
    allowed_per_run_for, resolve_user_tier, resolved_monthly_credits,
)
from gtm_engine.storage.database import Database

DEFAULT_LIMITS: dict[str, int] = {
    "brave": 50,
    "groq": 200,
    "places": 20,
    # Campaign-run dispatches per day. Each run is also bounded by the per-run lead cap and the
    # one-run-at-a-time guard. On grydinteam (open-source) all runs share the repo's GitHub
    # Actions minutes, so this is tight by design.
    "runs": 3,
    # Per-user daily caps on the endpoints that each cost something on every call - an LLM parse,
    # an outbound SMTP connection, an outbound API probe - so none can be hammered unbounded.
    "nl": 20,
    "smtp_test": 30,
    "key_test": 30,
}


MAX_LIMITS: dict[str, int] = {k: v * 10 for k, v in DEFAULT_LIMITS.items()}

# Monthly caps, on top of the daily ones above. Only "runs" has one today: PRICING_PHASES_2.md's
# draft tier table promises "campaign runs/month" and this engine already treats run dispatches
# as tight-by-design (shared GitHub Actions minutes). Resources like places also have a monthly
# number in the pricing-tier table, but those are *per-tier* (Free=0, Pro=500, Enterprise=2500)
# and there is no billing/tier system yet to tell a paid account apart from a free one - setting
# a flat monthly cap on them now would silently disable enrichment for every current user. Add
# once Stripe billing lands and a user's tier is knowable.
MONTHLY_LIMITS: dict[str, int] = {
    "runs": 5,
}

MONTHLY_MAX_LIMITS: dict[str, int] = {k: v * 10 for k, v in MONTHLY_LIMITS.items()}


def _limit(resource: str, db: Database | None = None, user_id: str | None = None) -> int:
    if db and user_id:
        prefs = {r["pref_key"]: r["pref_value"] for r in db.get_preferences(user_id)}
        user_val = prefs.get(f"daily_limit_{resource}")
        if user_val and user_val.isdigit():
            cap = MAX_LIMITS.get(resource, 1000)
            return max(1, min(int(user_val), cap))
    env = os.environ.get(f"GTM_DAILY_LIMIT_{resource.upper()}")
    if env and env.isdigit():
        return int(env)
    return DEFAULT_LIMITS.get(resource, 100)


def _tier_monthly_runs(db: Database | None, user_id: str | None) -> int | None:
    """The monthly run allowance implied by the caller's tier. None if it can't be resolved.

    Credits - not runs - are the unit of charge (P2). A flat runs cap that binds *before* the
    credit allowance sells a tier the engine then refuses to deliver: the old flat 5/month let
    Enterprise reach only 500 of its 2,500 credits (20%) and Pro 250 of 500 (50%). The comment
    that justified the flat number ("no billing/tier system yet to tell a paid account apart
    from a free one") went stale when P4 landed resolve_user_tier().

    Deriving the cap from the tier's own credits at its SMALLEST run size guarantees the runs
    counter can never bind before credits do, whatever run size the user picks, while still
    leaving a finite backstop against dispatch spam. The daily runs limit and the tier's
    daily_credit_throttle remain the burst controls.
    """
    if not (db and user_id):
        return None
    tier = resolve_user_tier(db, user_id)
    smallest_run = min(allowed_per_run_for(tier) or [1]) or 1
    return math.ceil(resolved_monthly_credits(tier) / smallest_run)


def _monthly_limit(resource: str, db: Database | None = None, user_id: str | None = None) -> int | None:
    """None means no monthly cap for this resource."""
    if resource not in MONTHLY_LIMITS:
        return None
    # Precedence is unchanged (user preference -> operator env var -> default); only the
    # *default* for "runs" became tier-derived rather than one flat number for everyone.
    default = MONTHLY_LIMITS[resource]
    if resource == "runs":
        default = _tier_monthly_runs(db, user_id) or default
    if db and user_id:
        prefs = {r["pref_key"]: r["pref_value"] for r in db.get_preferences(user_id)}
        user_val = prefs.get(f"monthly_limit_{resource}")
        if user_val and user_val.isdigit():
            # Never let the override ceiling land below the tier's own default, or a paid user
            # editing this field could silently cap themselves under what they bought.
            cap = max(MONTHLY_MAX_LIMITS.get(resource, 1000), default)
            return max(1, min(int(user_val), cap))
    env = os.environ.get(f"GTM_MONTHLY_LIMIT_{resource.upper()}")
    if env and env.isdigit():
        return int(env)
    return default


def check_usage(db: Database, user_id: str | None, resource: str) -> bool:
    """Check if the user is under the daily (and monthly, where capped) limit and increment.
    Returns True if allowed."""
    if not user_id:
        return True
    return db.check_and_increment_usage(
        user_id, resource, _limit(resource, db, user_id),
        monthly_limit=_monthly_limit(resource, db, user_id),
    )


def get_all_usage(db: Database, user_id: str) -> dict[str, dict]:
    """Current usage for all resources, with limits."""
    rows = db.get_usage(user_id)
    daily_map = {r["resource"]: r["daily_count"] for r in rows}
    monthly_map = {r["resource"]: r["monthly_count"] for r in rows}
    result = {
        resource: {
            "count": daily_map.get(resource, 0),
            "limit": _limit(resource, db, user_id),
            "default_limit": DEFAULT_LIMITS.get(resource, 100),
            "max_limit": MAX_LIMITS.get(resource, 1000),
        }
        for resource in DEFAULT_LIMITS
    }
    for resource in MONTHLY_LIMITS:
        result[resource]["monthly_count"] = monthly_map.get(resource, 0)
        result[resource]["monthly_limit"] = _monthly_limit(resource, db, user_id)
        result[resource]["monthly_default_limit"] = MONTHLY_LIMITS.get(resource)
        result[resource]["monthly_max_limit"] = MONTHLY_MAX_LIMITS.get(resource)
    return result
