"""Run allowance is charged only for successfully dispatched workflows."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from gtm_engine.api import main as api


@pytest.fixture
def run_db(monkeypatch):
    db = Mock()
    db.get_run_progress.return_value = None
    db.reserve_credits.return_value = "reservation"
    db.check_and_increment_usage.return_value = True
    monkeypatch.setattr(api, "_db", lambda: db)
    monkeypatch.setattr(api, "_campaign", lambda _: SimpleNamespace(
        max_companies=5, min_outreach_ready=None))
    monkeypatch.setattr(api, "_campaign_files", lambda: {})
    monkeypatch.setattr(api, "_is_unlimited", lambda _: False)
    monkeypatch.setattr(api, "resolve_user_tier", lambda *_: SimpleNamespace(name="free"))
    monkeypatch.setattr(api, "allowed_per_run_for", lambda _: [3, 5, 10])
    monkeypatch.setattr(api, "credit_status", lambda *_: {
        "monthly_remaining": 30, "daily_remaining": 10})
    # Keep the real check_usage/atomic storage call, with deterministic limits.
    monkeypatch.setattr("gtm_engine.api.usage._limit", lambda *_: 3)
    monkeypatch.setattr("gtm_engine.api.usage._monthly_limit", lambda *_: 10)
    monkeypatch.setattr(api, "dispatch_workflow", Mock())
    return db


def run(size=5):
    return api.run_campaign("camp", api.RunRequest(max_companies=size), "user", "user@example.com")


def test_invalid_run_does_not_consume_allowance(run_db):
    with pytest.raises(HTTPException) as error:
        run(50)
    assert error.value.status_code == 400
    run_db.check_and_increment_usage.assert_not_called()
    api.dispatch_workflow.assert_not_called()


def test_insufficient_credits_do_not_consume_allowance(run_db, monkeypatch):
    monkeypatch.setattr(api, "credit_status", lambda *_: {
        "monthly_remaining": 0, "daily_remaining": 0})
    with pytest.raises(HTTPException) as error:
        run()
    assert error.value.status_code == 402
    run_db.check_and_increment_usage.assert_not_called()
    api.dispatch_workflow.assert_not_called()


@pytest.mark.parametrize("failure", [HTTPException(502, "GitHub rejected dispatch"),
                                    RuntimeError("connection failed")])
def test_failed_dispatch_refunds_run_and_credits(run_db, failure):
    api.dispatch_workflow.side_effect = failure
    with pytest.raises(type(failure)) as error:
        run()
    assert error.value is failure
    charged_date = run_db.check_and_increment_usage.call_args.kwargs["usage_date"]
    run_db.refund_usage.assert_called_once_with("user", "runs", charged_date)
    run_db.release_reservation.assert_called_once_with("camp", "refunded")
    run_db.set_run_progress.assert_not_called()


def test_successful_dispatch_keeps_allowance(run_db):
    run()
    run_db.check_and_increment_usage.assert_called_once()
    api.dispatch_workflow.assert_called_once()
    run_db.refund_usage.assert_not_called()
    run_db.release_reservation.assert_not_called()
    run_db.set_run_progress.assert_called_once()


def test_quota_denial_refunds_credits_without_dispatch(run_db):
    run_db.check_and_increment_usage.return_value = False
    with pytest.raises(HTTPException) as error:
        run()
    assert error.value.status_code == 429
    api.dispatch_workflow.assert_not_called()
    run_db.refund_usage.assert_not_called()
    run_db.release_reservation.assert_called_once_with("camp", "refunded")


def test_github_not_found_explains_repository_access(monkeypatch):
    import httpx

    monkeypatch.setenv("GTM_GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("GTM_GITHUB_REPO", "owner/private-repo")
    monkeypatch.setattr(api.httpx, "post", lambda *a, **k: httpx.Response(
        404, json={"message": "Not Found"}))
    with pytest.raises(HTTPException) as error:
        api.dispatch_workflow("gather-leads.yml", {})
    assert error.value.status_code == 502
    assert "Actions: Read and write" in error.value.detail
    assert "owner/private-repo" in error.value.detail
    assert "test-token" not in error.value.detail


def test_stale_starting_progress_becomes_failed():
    from datetime import datetime, timedelta, timezone

    old = {"stage": "starting", "updated_at": (datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat()}
    failed = {**old, "stage": "failed"}
    db = Mock()
    db.get_run_progress.side_effect = [old, failed]
    assert api._current_run_progress(db, "camp") == failed
    db.fail_stale_run_progress.assert_called_once_with("camp", old["updated_at"])


def test_live_heartbeat_is_preserved():
    from datetime import datetime, timezone

    live = {"stage": "process", "updated_at": datetime.now(timezone.utc).isoformat()}
    db = Mock()
    db.get_run_progress.return_value = live
    assert api._current_run_progress(db, "camp") == live
    db.fail_stale_run_progress.assert_not_called()
