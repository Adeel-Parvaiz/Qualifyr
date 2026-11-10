"""P1 dynamic campaigns: slugging, id-or-path resolution, and DB-backed create/list/delete.

The pure helpers run anywhere; the API/DB tests are gated on GTM_TEST_DATABASE_URL like the
rest of the DB-backed suite."""

import pytest
from fastapi.testclient import TestClient

from conftest import bypass_auth
from gtm_engine.config.loader import resolve_campaign, slugify_campaign_id
from gtm_engine.storage.database import Database


# --- pure helpers (no DB) -----------------------------------------------------------------

def test_slugify_is_urlsafe_and_dedupes():
    assert slugify_campaign_id("Retail & Apparel, Lahore") == "retail-apparel-lahore"
    assert slugify_campaign_id("  ") == "campaign"
    existing = {"retail-apparel-lahore", "retail-apparel-lahore-2"}
    assert slugify_campaign_id("Retail & Apparel, Lahore", existing) == "retail-apparel-lahore-3"


def test_resolve_campaign_reads_a_yaml_path(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("campaign_id: acme-x\nname: Acme\noffer: widgets\nosm_categories: [shop=clothes]\n", encoding="utf-8")
    cfg = resolve_campaign(p)  # a real file path never touches the DB
    assert cfg.campaign_id == "acme-x" and cfg.name == "Acme"


# --- DB-backed: resolution by id, and the API create/list/delete cycle --------------------

def test_resolve_campaign_falls_back_to_db_by_id(settings):
    db = Database(settings.database_url)
    db.upsert_campaign("stored-1", "Stored", {"campaign_id": "stored-1", "name": "Stored", "offer": "x",
                                              "osm_categories": ["shop=clothes"]})
    db.close()
    cfg = resolve_campaign("stored-1", settings.database_url)   # not a file -> DB lookup
    assert cfg.campaign_id == "stored-1" and cfg.offer == "x"
    with pytest.raises(FileNotFoundError):
        resolve_campaign("does-not-exist", settings.database_url)


@pytest.fixture
def api_client(settings, tmp_path, monkeypatch):
    import gtm_engine.api.main as m
    monkeypatch.setattr(m, "_settings", settings)
    camp_dir = tmp_path / "campaigns"
    camp_dir.mkdir()
    monkeypatch.setattr(m, "CAMPAIGN_DIR", camp_dir)
    bypass_auth(m, monkeypatch)
    return TestClient(m.app)


def test_create_list_and_delete_campaign(api_client):
    body = {"name": "Retail & Apparel, Lahore", "offer": "inventory software",
            "countries": ["Pakistan"], "cities": ["Lahore"], "target_industries": ["retail"],
            "buyer_keywords": ["retailer"], "osm_categories": ["shop=clothes"],
            "overture_categories": [], "min_score": 70, "max_companies": 40}
    r = api_client.post("/campaigns", json=body)
    assert r.status_code == 201, r.text
    cid = r.json()["campaign_id"]
    assert cid == "retail-apparel-lahore"

    listed = api_client.get("/campaigns").json()
    row = next(c for c in listed if c["campaign_id"] == cid)
    assert row["file"] is None and row["cities"] == ["Lahore"] and row["max_companies"] == 40

    # A second campaign with the same name gets a distinct id.
    r2 = api_client.post("/campaigns", json=body)
    assert r2.json()["campaign_id"] == "retail-apparel-lahore-2"

    assert api_client.delete(f"/campaigns/{cid}").status_code == 204
    assert all(c["campaign_id"] != cid for c in api_client.get("/campaigns").json())


def test_create_rejects_empty_name_or_offer(api_client):
    assert api_client.post("/campaigns", json={"name": "", "offer": "x"}).status_code == 422
    assert api_client.post("/campaigns", json={"name": "X", "offer": "  "}).status_code == 422
