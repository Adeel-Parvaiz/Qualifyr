"""Client-ready CSV columns: confidence, catch-all surfacing, and the 'Exec contact' flag
that distinguishes premium leads (named decision-maker + reachable) from the broader
qualified-and-contactable set, without excluding anyone from the export."""

from gtm_engine.export.csv_export import CLEAN_COLUMNS, clean_row
from gtm_engine.models import EmailStatus, Lead


def _lead(**kw) -> Lead:
    defaults = dict(
        lead_id="l1", campaign_id="c1", company_name="Acme Retail",
        total_score=50, score_reason="high buyer fit",
    )
    defaults.update(kw)
    return Lead(**defaults)


def test_exec_contact_true_when_named_contact_plus_email():
    row = clean_row(_lead(contact_name="Ahmed Raza", contact_role="CEO",
                          contact_email="ahmed@acme.pk", email_status=EmailStatus.MX_VALID))
    assert row["Exec contact"] == "yes"


def test_exec_contact_true_when_named_contact_plus_phone_only():
    row = clean_row(_lead(contact_name="Ahmed Raza", contact_role="Director",
                          phone="+92-300-1234567"))
    assert row["Exec contact"] == "yes"


def test_exec_contact_empty_when_no_name():
    row = clean_row(_lead(contact_email="info@acme.pk", phone="+92-300-1234567"))
    assert row["Exec contact"] == ""


def test_exec_contact_empty_when_name_but_no_channel():
    row = clean_row(_lead(contact_name="Ahmed Raza", contact_role="CEO"))
    assert row["Exec contact"] == ""


def test_clean_columns_contains_exec_contact():
    assert "Exec contact" in CLEAN_COLUMNS
