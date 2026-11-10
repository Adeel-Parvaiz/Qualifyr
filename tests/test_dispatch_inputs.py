"""What the API hands to GitHub Actions, and when it refuses to hand anything over.

Both behaviours here failed in production and neither was visible locally: the dispatch
crosses a machine boundary, so a path that resolves on the API host means nothing on the
runner, and a run that dies without reporting leaves state only the *next* request sees.
"""

from datetime import datetime, timedelta, timezone

import pytest

from gtm_engine.api.main import _run_is_active, _workflow_campaign_path


def _ago(**kw) -> dict:
    return {"stage": "crawl", "updated_at": (datetime.now(timezone.utc) - timedelta(**kw)).isoformat()}


def test_campaign_path_is_relative_to_the_repo():
    path = _workflow_campaign_path("retail-isb-001")
    # The runner resolves this against its own checkout. An absolute path from the API
    # host (on Vercel, /var/task/...) exists nowhere there.
    assert not path.startswith("/")
    assert ":" not in path, "a Windows drive letter would not resolve on the runner either"
    assert path.startswith("config/campaigns/")
    assert "\\" not in path, "must be posix-separated regardless of the API host's OS"


def test_unknown_campaign_still_raises_keyerror():
    with pytest.raises(KeyError):
        _workflow_campaign_path("no-such-campaign")


@pytest.mark.parametrize("live", [None, {}, {"stage": None}, {"stage": "completed"}, {"stage": "failed"}])
def test_absent_or_finished_runs_are_not_active(live):
    assert not _run_is_active(live)


def test_a_recently_reporting_run_is_active():
    assert _run_is_active(_ago(minutes=1))


def test_a_silent_run_goes_stale_so_the_campaign_is_not_wedged_forever():
    # The exact case seen in production: three campaigns stuck at "starting" because the
    # Actions job failed before writing any terminal state.
    assert not _run_is_active(_ago(hours=3))
    assert not _run_is_active({"stage": "starting", "updated_at": _ago(minutes=30)["updated_at"]})


def test_unparseable_timestamp_is_treated_as_active():
    # Dispatching a second crawl over a live one is worse than a spurious 409.
    assert _run_is_active({"stage": "crawl", "updated_at": "not a date"})
    assert _run_is_active({"stage": "crawl"})
