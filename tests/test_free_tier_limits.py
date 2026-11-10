"""Free-tier product limits: at most 3 campaigns per user, at most 10 leads per campaign.

Local/self-host operators (no auth, user_id None) are unlimited; these tests simulate a
signed-in user by overriding current_user_id.
"""

import pytest
from fastapi.testclient import TestClient

from conftest import bypass_auth
from gtm_engine.storage.database import Database


@pytest.fixture
def client(settings, tmp_path, monkeypatch):
    import gtm_engine.api.main as m
    from gtm_engine.api.auth import current_user_id
    monkeypatch.setattr(m, "_settings", settings)
    camp_dir = tmp_path / "campaigns"
    camp_dir.mkdir()
    monkeypatch.setattr(m, "CAMPAIGN_DIR", camp_dir)
    # Make sure the DB schema exists.
    Database(settings.database_url).close()
    bypass_auth(m, monkeypatch)
    # Simulate a signed-in user so the per-user quota applies.
    m.app.dependency_overrides[current_user_id] = lambda: "user-free-1"
    yield TestClient(m.app)
    m.app.dependency_overrides.pop(current_user_id, None)


def _create(client, name, max_companies=50):
    return client.post("/campaigns", json={
        "name": name, "offer": "x", "countries": ["Pakistan"], "provinces": [],
        "cities": ["Lahore"], "target_industries": [], "buyer_keywords": [],
        "osm_categories": [], "overture_categories": [], "min_score": 70,
        "max_companies": max_companies,
    })


def test_fourth_campaign_is_blocked(client):
    for i in range(3):
        assert _create(client, f"Camp {i}").status_code == 201
    r = _create(client, "Camp 4")
    assert r.status_code == 403
    assert "limited to 3 campaigns" in r.json()["detail"]


def test_max_companies_capped_at_ten_on_create(client):
    r = _create(client, "Capped", max_companies=200)
    assert r.status_code == 201
    cid = r.json()["campaign_id"]
    detail = client.get(f"/campaigns/{cid}").json()
    assert detail["max_companies"] == 10


def test_health_exposes_limits(client):
    limits = client.get("/health").json()["limits"]
    assert limits == {"max_campaigns": 3, "max_leads_per_campaign": 10}


def test_my_limits_reports_free_tier(client):
    r = client.get("/settings/limits").json()
    assert r == {
        "unlimited": False, "max_campaigns": 3, "max_leads_per_campaign": 10,
        "tier": "free", "allowed_leads_per_run": [3, 5, 10],
    }


def test_run_rejects_leads_per_run_outside_free_tier(client):
    cid = _create(client, "Run Me").json()["campaign_id"]
    r = client.post(f"/campaigns/{cid}/run", json={"max_companies": 50})
    assert r.status_code == 400
    assert "free tier" in r.json()["detail"]


def test_run_accepts_leads_per_run_within_free_tier(client, monkeypatch):
    import gtm_engine.api.main as m
    monkeypatch.setattr(m, "dispatch_workflow", lambda *a, **k: None)
    cid = _create(client, "Run Me Too").json()["campaign_id"]
    r = client.post(f"/campaigns/{cid}/run", json={"max_companies": 5})
    assert r.status_code == 200, r.text


def test_tier_preference_is_not_user_settable(client):
    r = client.put("/settings/preferences/tier", json={"value": "enterprise"})
    assert r.status_code == 403
    # Confirms it didn't silently write through: limits still report free.
    assert client.get("/settings/limits").json()["tier"] == "free"


def test_pro_tier_unlocks_bigger_leads_per_run(client, settings, monkeypatch):
    import gtm_engine.api.main as m
    monkeypatch.setattr(m, "dispatch_workflow", lambda *a, **k: None)
    db = Database(settings.database_url)
    db.set_preference("user-free-1", "tier", "pro")
    db.close()
    r = client.get("/settings/limits").json()
    assert r["tier"] == "pro"
    assert 50 in r["allowed_leads_per_run"]
    cid = _create(client, "Pro Run").json()["campaign_id"]
    assert client.post(f"/campaigns/{cid}/run", json={"max_companies": 50}).status_code == 200


def test_consume_credits_tracked_monthly(settings):
    """P2: Database.consume_credits accumulates against the shared usage_counts table and is
    readable back via the generic get_usage (same path /settings/usage reports from)."""
    db = Database(settings.database_url)
    db.consume_credits("user-credits-1", 4)
    db.consume_credits("user-credits-1", 3)
    rows = {r["resource"]: r for r in db.get_usage("user-credits-1")}
    assert rows["credits"]["monthly_count"] == 7
    assert rows["credits"]["daily_count"] == 7
    db.close()


def test_run_rejected_when_monthly_credits_exhausted(client, settings):
    """P2 pre-run gate: a free-tier user who has already consumed their monthly credit
    allowance (30) is blocked from starting a new run, even one that fits the leads-per-run
    dropdown."""
    db = Database(settings.database_url)
    db.consume_credits("user-free-1", 30)
    db.close()
    cid = _create(client, "No Credits Left").json()["campaign_id"]
    r = client.post(f"/campaigns/{cid}/run", json={"max_companies": 3})
    assert r.status_code == 402
    assert "credits" in r.json()["detail"]


def test_run_allowed_within_remaining_credits(client, settings, monkeypatch):
    import gtm_engine.api.main as m
    monkeypatch.setattr(m, "dispatch_workflow", lambda *a, **k: None)
    db = Database(settings.database_url)
    db.consume_credits("user-free-1", 5)  # free tier: 10/day, 30/month - 5 leaves room for 5 more today
    db.close()
    cid = _create(client, "Just Enough Credits").json()["campaign_id"]
    r = client.post(f"/campaigns/{cid}/run", json={"max_companies": 5})
    assert r.status_code == 200, r.text


def test_credits_surfaced_in_settings_usage(client, settings):
    db = Database(settings.database_url)
    db.consume_credits("user-free-1", 6)
    db.close()
    r = client.get("/settings/usage").json()
    assert r["credits"]["tier"] == "free"
    assert r["credits"]["monthly_used"] == 6
    assert r["credits"]["monthly_limit"] == 30
    assert r["credits"]["monthly_remaining"] == 24


@pytest.fixture
def master_client(settings, tmp_path, monkeypatch):
    """A client acting as a master (unlimited) account via the UNLIMITED_EMAILS allowlist."""
    import gtm_engine.api.main as m
    from gtm_engine.api.auth import current_user_email, current_user_id
    monkeypatch.setattr(m, "_settings", settings)
    monkeypatch.setattr(m, "UNLIMITED_EMAILS", {"boss@example.com"})
    camp_dir = tmp_path / "campaigns"
    camp_dir.mkdir()
    monkeypatch.setattr(m, "CAMPAIGN_DIR", camp_dir)
    Database(settings.database_url).close()
    bypass_auth(m, monkeypatch)
    m.app.dependency_overrides[current_user_id] = lambda: "user-master"
    m.app.dependency_overrides[current_user_email] = lambda: "boss@example.com"
    yield TestClient(m.app)
    m.app.dependency_overrides.pop(current_user_id, None)
    m.app.dependency_overrides.pop(current_user_email, None)


def test_master_account_is_unlimited(master_client):
    # Well past the 3-campaign cap.
    for i in range(6):
        assert _create(master_client, f"Master {i}", max_companies=200).status_code == 201
    r = master_client.get("/settings/limits").json()
    assert r["unlimited"] is True
    # Lead cap is not clamped for a master account.
    cid = _create(master_client, "Big", max_companies=200).json()["campaign_id"]
    assert master_client.get(f"/campaigns/{cid}").json()["max_companies"] == 200


def test_delete_is_soft_and_row_persists(client, settings):
    """Deleting a campaign is a soft delete: it leaves the UI list but its row stays in the
    database permanently. A later create never resurrects it (fresh id)."""
    cid = _create(client, "Keep Me Forever").json()["campaign_id"]
    assert any(c["campaign_id"] == cid for c in client.get("/campaigns").json())

    assert client.delete(f"/campaigns/{cid}").status_code == 204

    # Gone from the list the user sees...
    assert not any(c["campaign_id"] == cid for c in client.get("/campaigns").json())
    # ...but the row is still in the database, for good.
    db = Database(settings.database_url)
    assert cid in db.all_campaign_ids()
    assert not any(c["campaign_id"] == cid for c in db.list_campaigns())
    db.close()

    # Recreating with the same name gets a NEW id – the kept row is never overwritten.
    cid2 = _create(client, "Keep Me Forever").json()["campaign_id"]
    assert cid2 != cid


def test_nl_honours_discovery_hints(client):
    """OSM/search hints from the NL form pin discovery scope – the pipeline treats user map
    categories as authoritative, so e.g. a doctors search never re-broadens to pharmacies."""
    r = client.post("/campaigns/nl", json={
        "text": "find doctors in G-11 Islamabad",
        "osm_categories": ["amenity=doctors", "amenity=clinic"],
        "search_queries": ["doctors G-11 Islamabad"],
    })
    assert r.status_code == 201, r.text
    cfg = client.get(f"/campaigns/{r.json()['campaign_id']}").json()
    assert cfg["osm_categories"] == ["amenity=doctors", "amenity=clinic"]
    assert "doctors G-11 Islamabad" in cfg["search_queries"]
