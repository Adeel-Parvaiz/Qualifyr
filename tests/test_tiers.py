"""P4 tier dataclass: three tiers, resolver with fallback, env-var back-compat for free tier."""

from gtm_engine.pricing.tiers import (
    ENTERPRISE, FREE, PRO, TIERS, allowed_per_run_for, resolved_max_campaigns,
    resolved_monthly_credits, tier_for,
)


def test_three_tiers_only():
    assert set(TIERS.keys()) == {"free", "pro", "enterprise"}


def test_prices_match_ceo_direction():
    assert FREE.price_usd_per_month == 0
    assert PRO.price_usd_per_month == 30
    assert ENTERPRISE.price_usd_per_month == 50


def test_free_is_byok_only():
    assert FREE.byok_only is True
    assert PRO.byok_only is False
    assert ENTERPRISE.byok_only is False


def test_credits_scale_with_tier():
    assert FREE.monthly_credits < PRO.monthly_credits < ENTERPRISE.monthly_credits


def test_allowed_per_run_includes_3_5_10_everywhere():
    for t in TIERS.values():
        assert {3, 5, 10}.issubset(set(t.allowed_leads_per_run))


def test_pro_and_enterprise_have_bigger_per_run_options():
    assert 50 in PRO.allowed_leads_per_run
    assert 100 in ENTERPRISE.allowed_leads_per_run
    assert 100 not in PRO.allowed_leads_per_run


def test_tier_for_resolves_known_names_case_insensitive():
    assert tier_for("free") is FREE
    assert tier_for("PRO") is PRO
    assert tier_for("Enterprise") is ENTERPRISE


def test_tier_for_falls_back_to_free_on_unknown_or_none():
    assert tier_for(None) is FREE
    assert tier_for("") is FREE
    assert tier_for("unknown_tier") is FREE


def test_env_override_for_free_max_campaigns(monkeypatch):
    monkeypatch.setenv("GTM_FREE_MAX_CAMPAIGNS", "7")
    assert resolved_max_campaigns(FREE) == 7
    # Paid tiers are not overrideable via this env var.
    assert resolved_max_campaigns(PRO) == PRO.max_campaigns


def test_env_override_for_free_monthly_credits(monkeypatch):
    monkeypatch.setenv("GTM_FREE_MONTHLY_CREDITS", "100")
    assert resolved_monthly_credits(FREE) == 100
    assert resolved_monthly_credits(ENTERPRISE) == ENTERPRISE.monthly_credits


def test_allowed_per_run_is_sorted():
    for t in TIERS.values():
        assert allowed_per_run_for(t) == sorted(allowed_per_run_for(t))


def test_tier_is_frozen_dataclass():
    import dataclasses
    assert dataclasses.is_dataclass(FREE)
    # Frozen means attempts to mutate raise FrozenInstanceError.
    import pytest
    with pytest.raises(Exception):
        FREE.monthly_credits = 999  # type: ignore[misc]


# --- the monthly runs cap must never bind before credits do -------------------------
#
# Regression (found 2026-10-11): MONTHLY_LIMITS["runs"] was a flat 5/month for everyone,
# predating the tier system. Enterprise could reach only 500 of its 2,500 credits (20%)
# and Pro 250 of 500 (50%) - we were selling capacity the engine then refused to serve.


class _FakeDB:
    """Minimal stand-in: _monthly_limit only needs get_preferences()."""

    def __init__(self, tier: str | None = None, prefs: dict | None = None):
        self._rows = [{"pref_key": k, "pref_value": v} for k, v in (prefs or {}).items()]
        if tier:
            self._rows.append({"pref_key": "tier", "pref_value": tier})

    def get_preferences(self, user_id):  # noqa: ARG002 - signature parity only
        return self._rows


def test_every_tier_can_spend_all_its_credits():
    """The invariant: runs_cap * smallest_run_size >= the tier's whole credit allowance."""
    from gtm_engine.api.usage import _monthly_limit

    for name, tier in TIERS.items():
        cap = _monthly_limit("runs", _FakeDB(name), "u1")
        smallest = min(allowed_per_run_for(tier))
        reachable = cap * smallest
        assert reachable >= resolved_monthly_credits(tier), (
            f"{name}: runs cap {cap} x {smallest} leads = {reachable} credits reachable, "
            f"but the tier sells {resolved_monthly_credits(tier)}"
        )


def test_paid_tiers_get_more_runs_than_the_old_flat_cap():
    from gtm_engine.api.usage import MONTHLY_LIMITS, _monthly_limit

    old_flat = MONTHLY_LIMITS["runs"]
    assert _monthly_limit("runs", _FakeDB("pro"), "u1") > old_flat
    assert _monthly_limit("runs", _FakeDB("enterprise"), "u1") > old_flat


def test_runs_cap_still_scales_with_tier():
    from gtm_engine.api.usage import _monthly_limit

    free = _monthly_limit("runs", _FakeDB("free"), "u1")
    pro = _monthly_limit("runs", _FakeDB("pro"), "u1")
    ent = _monthly_limit("runs", _FakeDB("enterprise"), "u1")
    assert free < pro < ent


def test_unresolvable_user_falls_back_to_flat_default():
    """Local operator / no signed-in user keeps the original conservative number."""
    from gtm_engine.api.usage import MONTHLY_LIMITS, _monthly_limit

    assert _monthly_limit("runs", None, None) == MONTHLY_LIMITS["runs"]


def test_user_override_ceiling_never_lands_below_the_tier_default():
    """A paid user editing their own monthly limit must not be able to cap themselves
    under what they bought - the old 10x-of-flat ceiling (50) was far below Enterprise."""
    from gtm_engine.api.usage import _monthly_limit

    tier_default = _monthly_limit("runs", _FakeDB("enterprise"), "u1")
    asked_for_more = _monthly_limit(
        "runs", _FakeDB("enterprise", {"monthly_limit_runs": str(tier_default + 100)}), "u1"
    )
    assert asked_for_more >= tier_default


def test_operator_env_var_still_wins(monkeypatch):
    from gtm_engine.api.usage import _monthly_limit

    monkeypatch.setenv("GTM_MONTHLY_LIMIT_RUNS", "9")
    assert _monthly_limit("runs", _FakeDB("enterprise"), "u1") == 9
