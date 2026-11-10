"""F3 (2026-10-11): credits are reserved at dispatch, not just gated.

The pre-run gate runs in the API when a run is dispatched; billing happens in the GitHub
Actions runner minutes-to-hours later. Without a reservation, two concurrent dispatches on
different campaigns both read the same pre-consumption balance, both pass the gate, and
together overspend the tier. This was previously masked by a flat 5-runs/month cap; making
that cap tier-aware (F2) removed the mask, so the hold has to be real.

Semantics (confirmed with the CEO 2026-10-11): reserve the run's requested size, refund on a
recorded failure, and let anything older than the TTL stop counting so a runner that is
killed never strands a user's credits.
"""

from __future__ import annotations

import pytest

from gtm_engine.pricing.tiers import FREE, credit_status
from gtm_engine.storage import database as db_module
from gtm_engine.storage.database import Database


def test_reserve_then_reserved_credits_reflects_the_hold(pg_schema):
    db = Database(pg_schema())
    assert db.reserved_credits("u1") == 0
    rid = db.reserve_credits("u1", "camp-a", 10)
    assert rid is not None
    assert db.reserved_credits("u1") == 10


def test_reservations_are_scoped_per_user(pg_schema):
    db = Database(pg_schema())
    db.reserve_credits("u1", "camp-a", 10)
    assert db.reserved_credits("u2") == 0


def test_credit_status_counts_a_hold_as_used(pg_schema):
    """The core of the fix: an in-flight run shrinks the balance the next gate sees."""
    db = Database(pg_schema())
    before = credit_status(db, "u1", FREE)
    db.reserve_credits("u1", "camp-a", 10)
    after = credit_status(db, "u1", FREE)

    assert before["monthly_remaining"] - after["monthly_remaining"] == 10
    assert after["reserved"] == 10
    assert after["monthly_used"] == 10


def test_second_concurrent_dispatch_cannot_reuse_the_same_balance(pg_schema):
    """The race this exists to close. FREE allows 10 credits/day; two 10-lead runs
    dispatched back-to-back must not both find room."""
    db = Database(pg_schema())
    first = credit_status(db, "u1", FREE)
    assert first["daily_remaining"] >= 10, "precondition: the first run fits"

    db.reserve_credits("u1", "camp-a", 10)

    second = credit_status(db, "u1", FREE)
    assert second["daily_remaining"] < 10, (
        "a second concurrent dispatch still sees room for a full run - the hold is not counted"
    )


def test_release_consumed_frees_the_hold(pg_schema):
    db = Database(pg_schema())
    db.reserve_credits("u1", "camp-a", 10)
    assert db.release_reservation("camp-a", "consumed") == 1
    assert db.reserved_credits("u1") == 0


def test_release_refunded_frees_the_hold(pg_schema):
    db = Database(pg_schema())
    db.reserve_credits("u1", "camp-a", 10)
    assert db.release_reservation("camp-a", "refunded") == 1
    assert db.reserved_credits("u1") == 0


def test_release_only_touches_its_own_campaign(pg_schema):
    db = Database(pg_schema())
    db.reserve_credits("u1", "camp-a", 10)
    db.reserve_credits("u1", "camp-b", 5)
    db.release_reservation("camp-a", "consumed")
    assert db.reserved_credits("u1") == 5


def test_release_is_idempotent(pg_schema):
    """The runner may release a hold the API already refunded on a dispatch error."""
    db = Database(pg_schema())
    db.reserve_credits("u1", "camp-a", 10)
    assert db.release_reservation("camp-a", "consumed") == 1
    assert db.release_reservation("camp-a", "consumed") == 0
    assert db.reserved_credits("u1") == 0


def test_release_rejects_an_unknown_state(pg_schema):
    db = Database(pg_schema())
    with pytest.raises(ValueError):
        db.release_reservation("camp-a", "something-else")


def test_stale_holds_stop_counting_after_the_ttl(pg_schema, monkeypatch):
    """A runner that is killed never releases its hold; the TTL must reclaim it so the
    user is not locked out of credits they were never billed for."""
    db = Database(pg_schema())
    db.reserve_credits("u1", "camp-a", 10)
    assert db.reserved_credits("u1") == 10

    monkeypatch.setattr(db_module, "CREDIT_RESERVATION_TTL_HOURS", 0)
    assert db.reserved_credits("u1") == 0
    assert credit_status(db, "u1", FREE)["monthly_remaining"] == FREE.monthly_credits


def test_expire_stale_marks_rows_so_operators_can_see_them(pg_schema, monkeypatch):
    db = Database(pg_schema())
    db.reserve_credits("u1", "camp-a", 10)
    monkeypatch.setattr(db_module, "CREDIT_RESERVATION_TTL_HOURS", 0)
    assert db.expire_stale_reservations() == 1
    # Already expired: a second sweep is a no-op.
    assert db.expire_stale_reservations() == 0


def test_no_hold_without_a_user_or_a_positive_amount(pg_schema):
    """Local operator runs (user_id None) have no tier to bill, so nothing is held."""
    db = Database(pg_schema())
    assert db.reserve_credits("", "camp-a", 10) is None
    assert db.reserve_credits("u1", "camp-a", 0) is None
    assert db.reserve_credits("u1", "camp-a", -5) is None
    assert db.reserved_credits("u1") == 0


def test_hold_plus_spend_both_count_against_the_allowance(pg_schema):
    """A completed run that was billed, plus a second run still in flight."""
    db = Database(pg_schema())
    db.consume_credits("u1", 6)
    db.reserve_credits("u1", "camp-b", 4)
    status = credit_status(db, "u1", FREE)
    assert status["monthly_used"] == 10
    assert status["reserved"] == 4
    assert status["monthly_remaining"] == FREE.monthly_credits - 10
