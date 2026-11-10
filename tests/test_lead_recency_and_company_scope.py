"""2026-10-12: new leads were indistinguishable from old ones re-touched by a rerun, so the
Leads page (sorted by updated_at, which is restamped on every save) looked like a fresh query
had "overwritten" an older one - the old results were still there, just buried. Three fixes,
each covered here:

  1. leads.first_seen_at is set once at INSERT and never touched again, so sorting by it
     reliably puts genuinely new leads on top without old-but-reconfirmed ones jumping the queue.
  2. Pipeline.RunStats.new_leads counts leads never seen before in a campaign (companies.py has
     its own narrower coverage; this file only tests the DB-level contract that counter relies on).
  3. companies.company_key moved from a global PK to (campaign_id, company_key), so two campaigns
     discovering the same real-world business each get an independent row instead of the second
     one stealing the first's row via campaign_id reassignment."""

from __future__ import annotations

import pytest

from gtm_engine.models import CompanyType, Lead, Priority
from gtm_engine.storage.database import Database


def _lead(cid: str, name: str, score: int = 50) -> Lead:
    return Lead(campaign_id=cid, company_name=name, domain=f"{name}.pk".lower(),
                company_type=CompanyType.BUYER, total_score=score, priority=Priority.QUALIFIED)


# ---------------------------------------------------------------------------
# first_seen_at
# ---------------------------------------------------------------------------

def test_first_seen_at_is_set_on_insert(pg_schema):
    db = Database(pg_schema())
    lead = _lead("camp-a", "Acme")
    db.save_lead(lead, run_id="run-1", company_key="acme-key")
    row = db._execute("SELECT first_seen_at, updated_at FROM leads WHERE lead_id = %s",
                       (lead.lead_id,)).fetchone()
    assert row["first_seen_at"] is not None
    assert row["first_seen_at"] == row["updated_at"]


def test_first_seen_at_survives_a_rerun_that_touches_updated_at(pg_schema):
    """The actual bug: a rerun must bump updated_at (it did work, something changed) without
    resetting first_seen_at (it is not a new discovery)."""
    db = Database(pg_schema())
    lead = _lead("camp-a", "Acme")
    db.save_lead(lead, run_id="run-1", company_key="acme-key")
    first_seen_1 = db._execute("SELECT first_seen_at FROM leads WHERE lead_id = %s",
                                (lead.lead_id,)).fetchone()["first_seen_at"]

    lead.total_score = 77  # pretend a rerun re-scored the same company
    db.save_lead(lead, run_id="run-2", company_key="acme-key")
    row = db._execute("SELECT first_seen_at, updated_at, total_score FROM leads WHERE lead_id = %s",
                       (lead.lead_id,)).fetchone()

    assert row["first_seen_at"] == first_seen_1
    assert row["total_score"] == 77


def test_recent_order_sorts_by_first_seen_at_not_updated_at(pg_schema):
    """A lead created first but re-touched (rerun) most recently must NOT outrank a lead that
    was genuinely just discovered - this is exactly what "new leads buried under old ones"
    looked like before first_seen_at existed."""
    db = Database(pg_schema())
    old_lead = _lead("camp-a", "OldCo")
    db.save_lead(old_lead, run_id="run-1", company_key="old-key")

    new_lead = _lead("camp-a", "NewCo")
    db.save_lead(new_lead, run_id="run-2", company_key="new-key")

    # Rerun: OldCo gets touched again (updated_at bumped) but was NOT newly discovered.
    old_lead.total_score = 60
    db.save_lead(old_lead, run_id="run-2", company_key="old-key")

    ordered = db.list_leads("camp-a", order="recent")
    names = [l.company_name for l in ordered]
    assert names[0] == "NewCo", f"expected the genuinely new lead on top, got {names}"


# ---------------------------------------------------------------------------
# companies per-campaign scoping
# ---------------------------------------------------------------------------

def _upsert(db, campaign_id, key="shared-biz"):
    return db.upsert_company(key, campaign_id, "Shared Biz", domain="shared.pk",
                             website="https://shared.pk", country="Pakistan", city="Lahore",
                             source="osm", source_url=None, raw={})


def test_same_company_key_two_campaigns_both_get_a_row(pg_schema):
    db = Database(pg_schema())
    assert _upsert(db, "camp-a") is True  # new for campaign A
    assert _upsert(db, "camp-b") is True  # new for campaign B too - different campaign, not a dup

    row_a = db._execute(
        "SELECT campaign_id FROM companies WHERE campaign_id = %s AND company_key = %s",
        ("camp-a", "shared-biz"),
    ).fetchone()
    row_b = db._execute(
        "SELECT campaign_id FROM companies WHERE campaign_id = %s AND company_key = %s",
        ("camp-b", "shared-biz"),
    ).fetchone()
    assert row_a is not None, "campaign A's row must still exist after campaign B discovered the same business"
    assert row_b is not None


def test_rerun_within_one_campaign_still_updates_in_place(pg_schema):
    """The fix must not break the existing single-campaign dedup behaviour."""
    db = Database(pg_schema())
    assert _upsert(db, "camp-a") is True
    assert _upsert(db, "camp-a") is False  # same campaign, same company -> conflict, not new

    count = db._execute(
        "SELECT COUNT(*) AS n FROM companies WHERE campaign_id = %s AND company_key = %s",
        ("camp-a", "shared-biz"),
    ).fetchone()["n"]
    assert count == 1
