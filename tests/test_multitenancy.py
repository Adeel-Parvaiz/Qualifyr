"""Multi-tenancy: campaigns (and their leads) are scoped to the account that created them.

One account must never see another's campaigns or leads; file-based examples and legacy
NULL-owner campaigns stay shared; with auth off (local operator) everything is visible, as
before multi-tenancy. DB-backed, so gated on GTM_TEST_DATABASE_URL like the rest of the suite.
"""

import pytest
from fastapi.testclient import TestClient

from conftest import bypass_auth
from gtm_engine.api.auth import current_user_id
from gtm_engine.models import CompanyType, EmailStatus, Lead, Priority
from gtm_engine.storage.database import Database


@pytest.fixture
def api(settings, tmp_path, monkeypatch):
    """A client plus a helper to act as a given user (or as the local operator, user=None)."""
    import gtm_engine.api.main as m
    monkeypatch.setattr(m, "_settings", settings)
    camp_dir = tmp_path / "campaigns"
    camp_dir.mkdir()
    # A shipped example campaign: shared, reachable by everyone regardless of owner.
    (camp_dir / "shared.yaml").write_text(
        "campaign_id: shared-example\nname: Shared\noffer: x\ngeography:\n  cities: [Lahore]\n"
        "osm_categories: [shop=clothes]\n", encoding="utf-8")
    monkeypatch.setattr(m, "CAMPAIGN_DIR", camp_dir)
    bypass_auth(m, monkeypatch)
    client = TestClient(m.app)

    def act_as(user_id):
        # setitem via monkeypatch so the override is reverted on teardown -- setting it
        # directly leaks the acting user into later tests and breaks their isolation.
        monkeypatch.setitem(m.app.dependency_overrides, current_user_id, lambda: user_id)
        return client

    return act_as


def _create(client, name="Acme retail") -> str:
    r = client.post("/campaigns", json={"name": name, "offer": "inventory software",
                                        "cities": ["Lahore"], "osm_categories": ["shop=clothes"]})
    assert r.status_code == 201, r.text
    return r.json()["campaign_id"]


def test_a_campaign_is_owned_and_hidden_from_other_accounts(api):
    a_cid = _create(api("user-a"))

    # A sees their campaign in the list; B does not.
    assert any(c["campaign_id"] == a_cid for c in api("user-a").get("/campaigns").json())
    assert all(c["campaign_id"] != a_cid for c in api("user-b").get("/campaigns").json())

    # B is refused every campaign-scoped route with 404 (existence not leaked, never 403).
    for method, path in [("get", f"/campaigns/{a_cid}"), ("get", f"/campaigns/{a_cid}/stats"),
                         ("get", f"/campaigns/{a_cid}/leads"), ("delete", f"/campaigns/{a_cid}")]:
        assert getattr(api("user-b"), method)(path).status_code == 404, path

    # A can reach their own campaign.
    assert api("user-a").get(f"/campaigns/{a_cid}").status_code == 200


def test_leads_are_scoped_through_their_campaign(api, settings):
    a_cid = _create(api("user-a"))
    db = Database(settings.database_url)
    db.save_lead(Lead(campaign_id=a_cid, company_name="Co", domain="co.pk", company_type=CompanyType.BUYER,
                      total_score=80, priority=Priority.HIGH, contact_email="a@co.pk",
                      email_status=EmailStatus.MX_VALID, outreach_ready=True), "r", "co.pk")
    lead_id = db.list_leads(a_cid)[0].lead_id
    db.close()

    assert api("user-a").get(f"/leads/{lead_id}").status_code == 200
    assert api("user-b").get(f"/leads/{lead_id}").status_code == 404
    # A lead-mutating route is scoped the same way.
    assert api("user-b").post(f"/leads/{lead_id}/review", json={"verdict": "correct"}).status_code == 404


def test_file_examples_and_legacy_campaigns_stay_shared(api, settings):
    # File-based example: visible and reachable by any account.
    assert api("user-b").get("/campaigns/shared-example").status_code == 200
    assert any(c["campaign_id"] == "shared-example" for c in api("user-b").get("/campaigns").json())

    # A legacy DB campaign created before ownership (NULL owner) is shared too.
    db = Database(settings.database_url)
    db.upsert_campaign("legacy-1", "Legacy", {"campaign_id": "legacy-1", "name": "Legacy", "offer": "x",
                                              "osm_categories": ["shop=clothes"]}, owner_id=None)
    db.close()
    assert api("user-b").get("/campaigns/legacy-1").status_code == 200


def test_local_operator_with_no_auth_sees_everything(api):
    a_cid = _create(api("user-a"))
    b_cid = _create(api("user-b"), name="Beta co")
    listed = {c["campaign_id"] for c in api(None).get("/campaigns").json()}
    assert {a_cid, b_cid} <= listed
    assert api(None).get(f"/campaigns/{a_cid}").status_code == 200
    assert api(None).get(f"/campaigns/{b_cid}").status_code == 200


def test_ownership_survives_a_re_upsert(api, settings):
    """A run re-upserts the campaign with owner_id=None; COALESCE must keep the owner."""
    a_cid = _create(api("user-a"))
    db = Database(settings.database_url)
    cfg = db.campaign_config(a_cid)
    db.upsert_campaign(a_cid, "Acme retail", cfg, owner_id=None)  # simulate a pipeline re-upsert
    assert db.campaign_owner(a_cid) == "user-a"
    db.close()
    assert api("user-b").get(f"/campaigns/{a_cid}").status_code == 404
