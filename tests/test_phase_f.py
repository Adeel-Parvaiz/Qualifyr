"""Phase F: Sheets mirror, suppression management, campaign YAML editor endpoints."""

import httpx
import pytest

from conftest import bypass_auth
from gtm_engine.export.sheets import EXTRA_COLUMNS, SheetsExporter, rows_for
from gtm_engine.models import CSV_COLUMNS, CompanyType, EmailStatus, Lead, Priority
from gtm_engine.storage.database import Database


def _lead(**kw) -> Lead:
    base = dict(campaign_id="test-retail", company_name="Zara Fabrics", domain="zarafabrics.pk", city="Islamabad",
                company_type=CompanyType.BUYER, total_score=88, priority=Priority.HIGH, contact_email="a@zarafabrics.pk",
                email_status=EmailStatus.MX_VALID, outreach_ready=True, phone_type="mobile", reply_label="interested")
    base.update(kw)
    return Lead(**base)


def test_rows_for_sheet_has_csv_columns_plus_extras():
    rows = rows_for([_lead(), _lead(company_name="MCC", domain_age_years=12.5)])
    assert rows[0] == CSV_COLUMNS + EXTRA_COLUMNS
    assert rows[1][CSV_COLUMNS.index("company_name")] == "Zara Fabrics"
    assert rows[1][len(CSV_COLUMNS) + EXTRA_COLUMNS.index("reply_label")] == "interested"
    assert rows[2][len(CSV_COLUMNS) + EXTRA_COLUMNS.index("domain_age_years")] == "12.5"
    assert all(len(r) == len(rows[0]) for r in rows)


def test_sheets_exporter_creates_tab_clears_and_writes():
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/sheet123") and request.method == "GET":
            return httpx.Response(200, json={"sheets": [{"properties": {"title": "Sheet1"}}]})
        return httpx.Response(200, json={})
    client = httpx.Client(transport=httpx.MockTransport(handler), headers={"Authorization": "Bearer t"})
    n = SheetsExporter("sheet123", "t", client=client).replace("retail-isb-001", rows_for([_lead(), _lead()]))
    assert n == 2
    assert calls[0] == ("GET", "/v4/spreadsheets/sheet123")
    assert calls[1] == ("POST", "/v4/spreadsheets/sheet123:batchUpdate")     # tab did not exist -> created
    assert calls[2][1].endswith(":clear") and calls[3][0] == "PUT"


def test_suppression_list_and_removal(settings):
    db = Database(settings.database_url)
    db.add_suppression("Info@X.pk", "email", "test")
    db.add_suppression("bad.pk", "domain")
    rows = db.list_suppressions()
    assert {r["value"] for r in rows} == {"info@x.pk", "bad.pk"}
    assert db.remove_suppression("INFO@x.pk") and not db.remove_suppression("nope@x.pk")
    assert not db.is_suppressed("info@x.pk") and db.is_suppressed("bad.pk")
    db.close()


@pytest.fixture
def client(settings, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import gtm_engine.api.main as m
    monkeypatch.setattr(m, "_settings", settings)
    camp_dir = tmp_path / "campaigns"
    camp_dir.mkdir()
    (camp_dir / "one.yaml").write_text("campaign_id: one\nname: One\noffer: x\ngeography:\n  cities: [Lahore]\nosm_categories: [shop=clothes]\n", encoding="utf-8")
    monkeypatch.setattr(m, "CAMPAIGN_DIR", camp_dir)
    bypass_auth(m, monkeypatch)
    return TestClient(m.app), camp_dir


def test_campaign_yaml_roundtrip_and_validation(client):
    c, camp_dir = client
    got = c.get("/campaigns/one/yaml").json()
    assert got["file"] == "one.yaml" and "campaign_id: one" in got["yaml"]
    bad = c.post("/campaigns/validate", json={"yaml": "campaign_id: two\nname: T\noffer: x\nweights: {review_band: 50, rating: 10, proximity_tier: 15, online_gap: 25, pain_evidence: 20}"}).json()
    assert bad["ok"] is False and "sum to 100" in bad["error"]
    ok = c.post("/campaigns/validate", json={"yaml": "campaign_id: two\nname: Two\noffer: x\ngeography:\n  cities: [Karachi]\nchamber_sources: [kcci]"}).json()
    assert ok["ok"] and ok["sources"] == ["kcci"]
    # mismatch between URL and YAML is refused
    assert c.put("/campaigns/one/yaml", json={"yaml": "campaign_id: two\nname: Two\noffer: x"}).status_code == 422
    # a new campaign_id is created as a DB campaign (not a file), so it works on the read-only
    # serverless filesystem; it appears alongside the file-based "one" and reloads from the DB.
    r = c.put("/campaigns/two/yaml", json={"yaml": "campaign_id: two\nname: Two\noffer: x\ngeography:\n  cities: [Karachi]\nchamber_sources: [kcci]\n"})
    assert r.status_code == 200 and r.json()["file"] is None
    assert not (camp_dir / "two.yaml").exists()
    assert {x["campaign_id"] for x in c.get("/campaigns").json()} == {"one", "two"}
    assert "two" in c.get("/campaigns/two/yaml").json()["yaml"]   # round-trips from the DB


def test_suppression_endpoints(client):
    c, _ = client
    assert c.post("/suppressions", json={"value": "Spam@X.pk", "reason": "asked"}).json()["value"] == "spam@x.pk"
    rows = c.get("/suppressions").json()
    assert rows[0]["value"] == "spam@x.pk" and rows[0]["kind"] == "email"
    assert c.delete("/suppressions/spam@x.pk").status_code == 200
    assert c.delete("/suppressions/spam@x.pk").status_code == 404


def test_sheets_status_and_guard(client, monkeypatch):
    c, _ = client
    monkeypatch.delenv("GTM_SHEETS_SPREADSHEET_ID", raising=False)
    assert c.get("/sheets/status").json()["configured"] is False
    assert c.post("/campaigns/one/export/sheets").status_code == 400


def test_sheets_access_token_wraps_bad_json_cleanly(monkeypatch):
    """A malformed credentials secret becomes one actionable line, not a JSONDecodeError."""
    import pytest
    from gtm_engine.export.sheets import access_token
    monkeypatch.setenv("GTM_SHEETS_CREDENTIALS_JSON", "{not json")
    with pytest.raises(RuntimeError, match="not valid JSON"):
        access_token()


def test_sheets_access_token_wraps_google_refresh_error(monkeypatch):
    """The live failure ('account not found') surfaces as an actionable RuntimeError naming the
    service account, not a raw google-auth traceback."""
    import pytest
    # google-auth is the optional `sheets` extra; CI's test job installs only [api,dev], so skip
    # there rather than fail on the import.
    service_account = pytest.importorskip("google.oauth2.service_account")
    from google.auth.exceptions import RefreshError
    from gtm_engine.export import sheets as sheets_export

    monkeypatch.setenv("GTM_SHEETS_CREDENTIALS_JSON",
                       '{"client_email": "svc@proj.iam.gserviceaccount.com", "project_id": "proj"}')

    class _Creds:
        token = None
        def refresh(self, _request):
            raise RefreshError("invalid_grant: Invalid grant: account not found")

    # access_token() imports service_account lazily and calls this classmethod, so patch it on
    # the real module.
    monkeypatch.setattr(service_account.Credentials, "from_service_account_info",
                        classmethod(lambda cls, info, scopes=None: _Creds()))
    with pytest.raises(RuntimeError, match="svc@proj.iam.gserviceaccount.com.*account not found"):
        sheets_export.access_token()
