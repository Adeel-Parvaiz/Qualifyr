"""Remove rows the test suite wrote into the real database.

Background: the per-test schema isolation silently failed for one run (Supabase's
pooler drops the `options=-csearch_path=` startup parameter, so every test resolved
in `public`). Fixed in storage/database.py, but the rows it already wrote are still
there - and `outreach.yml` runs on a cron against this same database, so fake leads
are not merely untidy.

Prints what it would delete and exits. Pass --apply to actually delete.

    .venv\\Scripts\\python.exe scripts\\clean_test_debris.py
    .venv\\Scripts\\python.exe scripts\\clean_test_debris.py --apply
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg

from gtm_engine.config.loader import load_settings

# What the fixtures create. tests/conftest.py uses campaign_id "test-retail"; the lead
# factories in test_mailboxes/test_outreach address contacts as owner@coN.pk. Narrow on
# purpose - a broad "delete where email like '%.pk'" would take real Pakistani prospects
# with it, which is the whole target market.
#
# Note `leads` has no domain column (that lives on `companies`), so leads are matched by
# campaign and contact_email only.
TEST_CAMPAIGNS = ["test-retail"]
TEST_EMAIL_PATTERN = r"^owner@co\d+\.pk$"

LEAD_MATCH = "campaign_id = ANY(%(campaigns)s) OR contact_email ~ %(email)s"

PREVIEW = [
    ("leads", f"SELECT count(*) FROM leads WHERE {LEAD_MATCH}"),
    ("outreach_events (of those leads)",
     f"SELECT count(*) FROM outreach_events WHERE lead_id IN "
     f"(SELECT lead_id FROM leads WHERE {LEAD_MATCH})"),
    ("drafts (of those leads)",
     f"SELECT count(*) FROM drafts WHERE lead_id IN "
     f"(SELECT lead_id FROM leads WHERE {LEAD_MATCH})"),
    ("companies", "SELECT count(*) FROM companies WHERE campaign_id = ANY(%(campaigns)s)"),
    # pages hangs off companies by company_key with no campaign_id of its own, so it is
    # invisible to every campaign-scoped filter here. Missed on the first pass and left
    # three orphans behind; match anything whose company is gone.
    ("pages (orphaned)",
     "SELECT count(*) FROM pages WHERE company_key NOT IN (SELECT company_key FROM companies)"),
    ("runs", "SELECT count(*) FROM runs WHERE campaign_id = ANY(%(campaigns)s)"),
    ("campaigns", "SELECT count(*) FROM campaigns WHERE campaign_id = ANY(%(campaigns)s)"),
    ("run_progress", "SELECT count(*) FROM run_progress WHERE campaign_id = ANY(%(campaigns)s)"),
    ("suppressions", "SELECT count(*) FROM suppressions WHERE value ~ %(email)s"),
]

DELETES = [
    f"DELETE FROM outreach_events WHERE lead_id IN (SELECT lead_id FROM leads WHERE {LEAD_MATCH})",
    f"DELETE FROM drafts WHERE lead_id IN (SELECT lead_id FROM leads WHERE {LEAD_MATCH})",
    f"DELETE FROM leads WHERE {LEAD_MATCH}",
    "DELETE FROM pages WHERE company_key IN "
    "(SELECT company_key FROM companies WHERE campaign_id = ANY(%(campaigns)s))",
    "DELETE FROM companies WHERE campaign_id = ANY(%(campaigns)s)",
    "DELETE FROM pages WHERE company_key NOT IN (SELECT company_key FROM companies)",
    "DELETE FROM run_progress WHERE campaign_id = ANY(%(campaigns)s)",
    "DELETE FROM runs WHERE campaign_id = ANY(%(campaigns)s)",
    "DELETE FROM campaigns WHERE campaign_id = ANY(%(campaigns)s)",
    "DELETE FROM suppressions WHERE value ~ %(email)s",
]


def main() -> None:
    apply = "--apply" in sys.argv
    dsn = load_settings().database_url
    if not dsn:
        sys.exit("GTM_DATABASE_URL is not set")

    args = {"campaigns": TEST_CAMPAIGNS, "email": TEST_EMAIL_PATTERN}

    with psycopg.connect(dsn, autocommit=False, prepare_threshold=None) as conn:
        print(f"{'what':36} {'rows':>6}")
        print("-" * 44)
        for label, sql in PREVIEW:
            print(f"{label:36} {conn.execute(sql, args).fetchone()[0]:>6}")

        # The point of the preview: show what survives, so an over-broad pattern is
        # obvious before it runs rather than after.
        print("\nleads that would be KEPT:")
        kept = conn.execute(
            f"SELECT campaign_id, count(*) FROM leads WHERE NOT ({LEAD_MATCH}) "
            "GROUP BY campaign_id ORDER BY 2 DESC",
            args,
        ).fetchall()
        for row in kept or [("(none)", 0)]:
            print(f"  {row[0]:34} {row[1]:>6}")

        if not apply:
            print("\nPreview only. Re-run with --apply to delete.")
            return

        # Children first: outreach_events and drafts reference leads by lead_id.
        for sql in DELETES:
            print(f"  {conn.execute(sql, args).rowcount:>6}  {sql.split(' WHERE')[0][12:]}")
        conn.commit()
        print("\ndeleted.")


if __name__ == "__main__":
    main()
