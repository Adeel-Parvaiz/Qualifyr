"""Credits are charged per outreach_ready lead (P2, 2026-10-10): cli.py bills
`result.stats.outreach_ready` at run completion. That counter is therefore a billing
figure, and it must describe the leads the user actually receives.

The bug this guards (found 2026-10-11): `_tally()` increments `stats.outreach_ready`
per company *during* the expansion rounds, but `_apply_hard_filters()` drops leads from
the returned set *afterwards*. Without a recompute between the two, any campaign carrying
hard filters bills for leads that were removed from its own results. NL campaigns set
hard filters routinely ("businesses lacking online presence"), so this fired in normal use.
"""

from __future__ import annotations

import inspect
import re

from gtm_engine.models import CompanyType, Lead, Priority
from gtm_engine.pipeline import Pipeline, _apply_hard_filters


def _lead(reviews: int | None, outreach_ready: bool) -> Lead:
    return Lead(
        campaign_id="t", company_name="X", domain="x.pk",
        company_type=CompanyType.BUYER, total_score=50,
        priority=Priority.QUALIFIED, outreach_ready=outreach_ready,
        online_presence={"google_review_count": reviews},
    )


def test_hard_filters_can_drop_outreach_ready_leads():
    """The precondition for the bug: a filter can remove a billable lead."""
    leads = [_lead(100, True), _lead(5, True), _lead(5, False)]
    pre_filter_billed = sum(1 for l in leads if l.outreach_ready)  # what _tally() counts

    delivered = _apply_hard_filters(leads, {"min_google_reviews": 50})
    delivered_billable = sum(1 for l in delivered if l.outreach_ready)

    assert pre_filter_billed == 2
    assert delivered_billable == 1, "filter must drop one outreach_ready lead for this scenario"
    assert pre_filter_billed != delivered_billable, (
        "if these ever match, this scenario no longer exercises the overbilling path"
    )


def test_run_recomputes_outreach_ready_after_hard_filters():
    """Guards the fix itself: the recompute must sit *after* the hard-filter block and
    *before* finish_run(), which is what persists the billed stats. This is a source-level
    assertion because the recompute is inline in the run() body - there is no seam to call
    it through without standing up a full pipeline (DB + network)."""
    src = inspect.getsource(Pipeline.run)

    i_filter = src.find("_apply_hard_filters")
    i_recompute = src.find("stats.outreach_ready = sum(")
    i_finish = src.find("finish_run(run_id")

    assert i_filter != -1, "hard-filter block disappeared - revisit this invariant"
    assert i_recompute != -1, (
        "stats.outreach_ready is no longer recomputed from the delivered leads; "
        "billing would revert to the pre-filter tally"
    )
    assert i_filter < i_recompute < i_finish, (
        "recompute must run after hard filtering and before the stats are persisted"
    )
    assert re.search(r"stats\.outreach_ready = sum\(1 for \w+ in leads if \w+\.outreach_ready\)", src), (
        "recompute must count the delivered `leads` list, not a pre-filter collection"
    )
