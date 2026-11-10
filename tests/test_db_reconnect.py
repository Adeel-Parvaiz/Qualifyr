"""E4: the DB layer transparently reconnects when the connection drops mid-run.

A long crawl outlives a pooler's idle recycle; without reconnect, every query after the drop
fails and the run silently ends as "0 leads, N errors". DB-backed."""

from gtm_engine.storage.database import Database


def _campaign(cid):
    return {"campaign_id": cid, "name": cid, "offer": "x", "osm_categories": ["shop=clothes"]}


def test_reads_reconnect_after_the_connection_drops(settings):
    db = Database(settings.database_url)
    db.upsert_campaign("recon-1", "Recon 1", _campaign("recon-1"))
    db.conn.close()  # simulate a pooler recycle / dropped connection
    rows = db.list_campaigns()  # must transparently reconnect, not raise
    assert any(r["campaign_id"] == "recon-1" for r in rows)
    db.close()


def test_writes_reconnect_after_the_connection_drops(settings):
    db = Database(settings.database_url)
    db.conn.close()
    db.upsert_campaign("recon-2", "Recon 2", _campaign("recon-2"))  # write after a drop
    assert any(r["campaign_id"] == "recon-2" for r in db.list_campaigns())
    db.close()


def test_search_path_is_reapplied_on_reconnect(settings):
    # settings.database_url carries options=-csearch_path=<test schema>. After a reconnect the
    # fresh connection must land back in that schema, or the row would vanish into public.
    db = Database(settings.database_url)
    db.upsert_campaign("recon-3", "Recon 3", _campaign("recon-3"))
    db.conn.close()
    assert db.campaign_config("recon-3") is not None  # resolved in the right schema post-reconnect
    db.close()
