from gtm_engine.api import usage as usage_api
from gtm_engine.storage.database import Database


def test_daily_cap_enforced_without_monthly_limit(pg_schema):
    db = Database(pg_schema())
    for _ in range(3):
        assert db.check_and_increment_usage("u1", "brave", limit=3) is True
    assert db.check_and_increment_usage("u1", "brave", limit=3) is False


def test_monthly_cap_enforced_independently_of_daily(pg_schema):
    db = Database(pg_schema())
    # Daily limit is generous; monthly limit of 2 should bite first.
    assert db.check_and_increment_usage("u1", "runs", limit=100, monthly_limit=2) is True
    assert db.check_and_increment_usage("u1", "runs", limit=100, monthly_limit=2) is True
    assert db.check_and_increment_usage("u1", "runs", limit=100, monthly_limit=2) is False


def test_daily_cap_bites_even_under_a_generous_monthly_cap(pg_schema):
    db = Database(pg_schema())
    assert db.check_and_increment_usage("u1", "runs", limit=1, monthly_limit=100) is True
    assert db.check_and_increment_usage("u1", "runs", limit=1, monthly_limit=100) is False


def test_get_usage_reports_daily_and_monthly_counts(pg_schema):
    db = Database(pg_schema())
    db.check_and_increment_usage("u1", "runs", limit=10, monthly_limit=10)
    db.check_and_increment_usage("u1", "runs", limit=10, monthly_limit=10)
    rows = {r["resource"]: r for r in db.get_usage("u1")}
    assert rows["runs"]["daily_count"] == 2
    assert rows["runs"]["monthly_count"] == 2


def test_check_usage_passes_through_monthly_limit_for_runs(pg_schema, monkeypatch):
    """check_usage really enforces the monthly cap (not just the daily one).

    Injected via the operator env var rather than by patching MONTHLY_LIMITS: since
    2026-10-11 the runs default is derived from the caller's tier (see
    _tier_monthly_runs), so patching the flat dict no longer sets the number for a
    user whose tier resolves. The env var still wins over the tier default.
    """
    db = Database(pg_schema())
    monkeypatch.setenv("GTM_MONTHLY_LIMIT_RUNS", "2")
    assert usage_api.check_usage(db, "u1", "runs") is True
    assert usage_api.check_usage(db, "u1", "runs") is True
    assert usage_api.check_usage(db, "u1", "runs") is False


def test_runs_monthly_default_is_tier_derived_not_the_flat_number(pg_schema):
    """A user with no tier row resolves to FREE, whose credit allowance implies more
    runs than the flat MONTHLY_LIMITS fallback. Regression guard for the bug where the
    flat 5/month capped Enterprise at 20% of the credits it had been sold."""
    db = Database(pg_schema())
    from gtm_engine.pricing.tiers import FREE, allowed_per_run_for, resolved_monthly_credits

    limit = usage_api._monthly_limit("runs", db, "u1")
    expected = -(-resolved_monthly_credits(FREE) // min(allowed_per_run_for(FREE)))  # ceil
    assert limit == expected
    assert limit != usage_api.MONTHLY_LIMITS["runs"]


def test_monthly_limit_none_for_resources_without_a_monthly_cap():
    # places intentionally has no monthly cap yet - see comment in api/usage.py.
    assert usage_api._monthly_limit("places") is None
    assert usage_api._monthly_limit("brave") is None
    # No db/user_id means the tier can't be resolved, so "runs" falls back to the flat
    # default. With a resolvable user it is tier-derived instead (see the test above).
    assert usage_api._monthly_limit("runs") == usage_api.MONTHLY_LIMITS["runs"]


def test_get_all_usage_includes_monthly_fields_only_for_capped_resources(pg_schema):
    db = Database(pg_schema())
    usage_api.check_usage(db, "u1", "runs")
    report = usage_api.get_all_usage(db, "u1")
    assert "monthly_count" in report["runs"]
    assert "monthly_limit" in report["runs"]
    assert "monthly_count" not in report["brave"]
