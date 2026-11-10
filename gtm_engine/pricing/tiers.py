"""Pricing tiers as data (P4 of the 2026-10-10 overhaul).

Three tiers, differing only in rate limits. Free uses the user's own API keys and gets a
small monthly credit allowance; Pro and Enterprise differ in monthly credits + max campaigns
+ allowed per-run lead counts.

Credits are the unit of charge (P2): 1 credit = 1 outreach_ready lead returned. A per-run
hard cap (P3) prevents infinite expansion loops.

Personalization is deterministic for every tier (P5, CEO 2026-10-10) and is NOT a
tier-differentiator.

Tier resolution: a user's tier comes from the `user_profiles.tier` column (default `"free"`).
Operator-settable until Stripe billing lands.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gtm_engine.storage.database import Database


@dataclass(frozen=True)
class Tier:
    name: str                           # "free" | "pro" | "enterprise"
    price_usd_per_month: int
    monthly_credits: int                # 1 credit = 1 outreach_ready lead returned
    daily_credit_throttle: int          # secondary safety ceiling to prevent one-day burns
    allowed_leads_per_run: list[int] = field(default_factory=list)  # dropdown choices for the Run button
    max_campaigns: int = 1
    byok_only: bool = False             # free tier uses user-provided API keys only


FREE = Tier(
    name="free",
    price_usd_per_month=0,
    monthly_credits=30,
    daily_credit_throttle=10,
    allowed_leads_per_run=[3, 5, 10],
    max_campaigns=3,
    byok_only=True,  # free users bring their own Brave/Groq/Places keys
)

PRO = Tier(
    name="pro",
    price_usd_per_month=30,
    monthly_credits=500,
    daily_credit_throttle=100,
    allowed_leads_per_run=[3, 5, 10, 25, 50],
    max_campaigns=25,
)

ENTERPRISE = Tier(
    name="enterprise",
    price_usd_per_month=50,
    monthly_credits=2500,
    daily_credit_throttle=250,
    allowed_leads_per_run=[3, 5, 10, 25, 50, 100],
    max_campaigns=100,
)

TIERS: dict[str, Tier] = {t.name: t for t in (FREE, PRO, ENTERPRISE)}


def tier_for(name: str | None) -> Tier:
    """Resolve a tier by name with a safe fallback to free. `None` / unknown -> FREE."""
    if not name:
        return FREE
    return TIERS.get(name.lower(), FREE)


def _env_override_int(env_var: str, default: int) -> int:
    """One-release migration helper: existing GTM_FREE_MAX_CAMPAIGNS / GTM_FREE_MAX_LEADS_PER_CAMPAIGN
    env vars still override free-tier values if set. Remove the overrides after one release cycle."""
    v = os.environ.get(env_var)
    return int(v) if v and v.isdigit() else default


def resolved_max_campaigns(tier: Tier) -> int:
    """Free tier can be overridden by GTM_FREE_MAX_CAMPAIGNS env var (back-compat)."""
    if tier.name == "free":
        return _env_override_int("GTM_FREE_MAX_CAMPAIGNS", tier.max_campaigns)
    return tier.max_campaigns


def resolved_monthly_credits(tier: Tier) -> int:
    """Free tier credit allowance can be overridden by GTM_FREE_MONTHLY_CREDITS (back-compat)."""
    if tier.name == "free":
        return _env_override_int("GTM_FREE_MONTHLY_CREDITS", tier.monthly_credits)
    return tier.monthly_credits


def allowed_per_run_for(tier: Tier) -> list[int]:
    """The per-run dropdown choices (P1). Returned sorted ascending."""
    return sorted(tier.allowed_leads_per_run)


# P1 (2026-10-10): no Stripe billing yet, so tier assignment is operator-set via the
# "tier" user_preferences row rather than a payment webhook. Deliberately NOT exposed
# through the generic PUT /settings/preferences/{pref_key} endpoint (see
# api/main.py's _RESERVED_PREF_KEYS) — a self-service write there would let any free
# user hand themselves Enterprise. Set it directly in the DB until billing lands.
def resolve_user_tier(db: "Database", user_id: str | None) -> Tier:
    """Resolve a user's tier from their `tier` preference row. No row / no user_id -> FREE."""
    if not user_id:
        return FREE
    prefs = {p["pref_key"]: p["pref_value"] for p in db.get_preferences(user_id)}
    return tier_for(prefs.get("tier"))


# P2 (2026-10-10): credits are the unit of charge - 1 credit = 1 outreach_ready lead
# returned (Database.consume_credits, called at run completion). This reads the same
# "credits" usage_counts row for the pre-run gate in api/main.py's run_campaign.
def credit_status(db: "Database", user_id: str, tier: Tier) -> dict:
    """This month's/today's credit consumption against the user's tier allowance.

    F3 (2026-10-11): spend that is already dispatched but not yet billed counts as used.
    Billing happens in the Actions runner at run completion, minutes-to-hours after the API
    gated the run, so without this a second concurrent dispatch would read the same
    pre-consumption balance and both runs would pass a gate only one of them could afford.
    Reservations are released when the run completes or fails, and stop counting after
    Database.CREDIT_RESERVATION_TTL_HOURS so an abandoned runner can't strand credits.

    Reserved credits count against the daily throttle as well as the monthly allowance: the
    TTL is 24h, so an active reservation is by definition recent enough to be today's spend.
    """
    rows = {r["resource"]: r for r in db.get_usage(user_id)}
    credits = rows.get("credits", {"daily_count": 0, "monthly_count": 0})
    reserved = db.reserved_credits(user_id)
    monthly_limit = resolved_monthly_credits(tier)
    daily_limit = tier.daily_credit_throttle
    monthly_used = credits["monthly_count"] + reserved
    daily_used = credits["daily_count"] + reserved
    return {
        "monthly_used": monthly_used,
        "monthly_limit": monthly_limit,
        "monthly_remaining": max(0, monthly_limit - monthly_used),
        "daily_used": daily_used,
        "daily_limit": daily_limit,
        "daily_remaining": max(0, daily_limit - daily_used),
        # Broken out so the UI can distinguish "spent" from "held by a run in flight".
        "reserved": reserved,
    }
