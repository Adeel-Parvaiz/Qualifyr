"""Sparse-area discovery fallback: when primary geo sources return too few rows for a
niche-city combo, the pipeline synthesizes "<category> in <city>" queries and runs
WebSearchDiscovery against them, even if the campaign carries no explicit search_queries.
Pure unit tests - all discovery sources stubbed, no DB/HTTP."""

import pytest

from gtm_engine.config.schema import CampaignConfig, GeographyConfig
from gtm_engine.models import DiscoveredCompany
from gtm_engine.pipeline import Pipeline, _sparse_fallback_queries


class _AsyncIter:
    def __init__(self, items):
        self.items = items

    def __aiter__(self):
        async def _gen():
            for i in self.items:
                yield i
        return _gen()


class _StubSource:
    """Minimal Discovery-shaped stub: has a .name and an async def discover() yielding fixed rows."""
    def __init__(self, name, companies):
        self.name = name
        self.companies = companies

    async def discover(self, campaign):
        for c in self.companies:
            yield c


def _mk_company(name, source="osm"):
    return DiscoveredCompany(name=name, country="Pakistan", city="Wah Cantt", source=source)


# --- _sparse_fallback_queries helper ----------------------------------------------------


def test_fallback_queries_cross_categories_with_areas():
    campaign = CampaignConfig(
        campaign_id="c",
        name="n",
        offer="o",
        osm_categories=["vet_clinic", "pet_grooming"],
        geography=GeographyConfig(cities=["Wah Cantt", "Taxila"]),
    )
    q = _sparse_fallback_queries(campaign, limit=10)
    assert "vet clinic in Wah Cantt" in q
    assert "pet grooming in Taxila" in q
    assert len(q) == 4


def test_fallback_queries_respects_limit():
    campaign = CampaignConfig(
        campaign_id="c", name="n", offer="o",
        osm_categories=["a", "b", "c"],
        geography=GeographyConfig(cities=["X", "Y", "Z"]),
    )
    q = _sparse_fallback_queries(campaign, limit=4)
    assert len(q) == 4


def test_fallback_queries_falls_back_through_category_sources():
    # No OSM categories - should fall back to overture_categories.
    campaign = CampaignConfig(
        campaign_id="c", name="n", offer="o",
        overture_categories=["clothing_store"],
        geography=GeographyConfig(cities=["Wah Cantt"]),
    )
    assert _sparse_fallback_queries(campaign, limit=10) == ["clothing store in Wah Cantt"]


def test_fallback_queries_empty_when_no_categories_or_areas():
    assert _sparse_fallback_queries(
        CampaignConfig(campaign_id="c", name="n", offer="o",
                       geography=GeographyConfig(cities=["X"])),
        limit=5,
    ) == []
    assert _sparse_fallback_queries(
        CampaignConfig(campaign_id="c", name="n", offer="o", osm_categories=["x"]),
        limit=5,
    ) == []


# --- Pipeline.discover() fallback wiring ------------------------------------------------


async def _run_discover(pipeline, campaign):
    return await pipeline.discover(campaign)


def _build_pipeline(settings, defaults, db, geo_rows, fallback_rows, monkeypatch):
    import gtm_engine.pipeline as pl

    class _StubGeocoder:
        async def bbox(self, *a, **k):
            return None

    monkeypatch.setattr(pl, "OvertureDiscovery", lambda f, s: _StubSource("overture", geo_rows))
    monkeypatch.setattr(pl, "OSMDiscovery", lambda f, s: _StubSource("osm", []))

    class _StubWebSearch:
        name = "websearch"
        last_queries = None

        def __init__(self, fetcher, settings, *, brave_api_key=None):
            pass

        async def discover(self, campaign):
            _StubWebSearch.last_queries = list(campaign.search_queries)
            for c in fallback_rows:
                yield c

    monkeypatch.setattr(pl, "WebSearchDiscovery", _StubWebSearch)

    class _FakeMX:
        async def has_mx(self, d):
            return False

    pipeline = pl.Pipeline(settings, defaults, db, fetcher=None, mx=_FakeMX())
    return pipeline, _StubWebSearch


async def test_fallback_fires_when_geo_count_below_threshold(settings, defaults, campaign, monkeypatch):
    settings.enable_sparse_fallback = True
    settings.enable_web_search_discovery = True
    settings.sparse_discovery_threshold = 10
    settings.sparse_fallback_max_queries = 3

    campaign.overture_categories = ["clothing_store"]
    campaign.osm_categories = ["shop=clothes"]
    campaign.geography = GeographyConfig(countries=["Pakistan"], cities=["Wah Cantt"])
    campaign.search_queries = []

    from gtm_engine.storage.database import Database
    db = Database(settings.database_url)

    geo = [_mk_company("Local1"), _mk_company("Local2")]  # < threshold
    fallback = [_mk_company("WebFound1", source="websearch"), _mk_company("WebFound2", source="websearch")]
    pipeline, StubWS = _build_pipeline(settings, defaults, db, geo, fallback, monkeypatch)

    out = await pipeline.discover(campaign)
    names = {c.name for c in out}
    assert "WebFound1" in names and "WebFound2" in names
    assert any(c.source == "websearch_sparse" for c in out)
    assert StubWS.last_queries is not None and len(StubWS.last_queries) > 0


async def test_fallback_skipped_when_geo_meets_threshold(settings, defaults, campaign, monkeypatch):
    settings.enable_sparse_fallback = True
    settings.enable_web_search_discovery = True
    settings.sparse_discovery_threshold = 3

    campaign.overture_categories = ["x"]
    campaign.osm_categories = ["shop=x"]
    campaign.geography = GeographyConfig(countries=["Pakistan"], cities=["Lahore"])

    from gtm_engine.storage.database import Database
    db = Database(settings.database_url)

    geo = [_mk_company(f"A{i}") for i in range(5)]
    pipeline, StubWS = _build_pipeline(settings, defaults, db, geo, [_mk_company("ShouldNotAppear")], monkeypatch)

    out = await pipeline.discover(campaign)
    assert "ShouldNotAppear" not in {c.name for c in out}
    assert StubWS.last_queries is None


async def test_fallback_disabled_toggle_blocks_it(settings, defaults, campaign, monkeypatch):
    settings.enable_sparse_fallback = False
    settings.enable_web_search_discovery = True

    campaign.overture_categories = ["x"]
    campaign.geography = GeographyConfig(countries=["Pakistan"], cities=["Wah Cantt"])

    from gtm_engine.storage.database import Database
    db = Database(settings.database_url)

    pipeline, StubWS = _build_pipeline(settings, defaults, db,
                                       geo_rows=[_mk_company("Only")],
                                       fallback_rows=[_mk_company("ShouldNotAppear")],
                                       monkeypatch=monkeypatch)

    out = await pipeline.discover(campaign)
    assert "ShouldNotAppear" not in {c.name for c in out}
    assert StubWS.last_queries is None


async def test_fallback_skips_queries_that_duplicate_campaign_search_queries(
        settings, defaults, campaign, monkeypatch):
    settings.enable_sparse_fallback = True
    settings.enable_web_search_discovery = True
    settings.sparse_discovery_threshold = 10
    settings.sparse_fallback_max_queries = 2

    campaign.overture_categories = ["clothing_store"]
    campaign.osm_categories = []
    campaign.geography = GeographyConfig(countries=["Pakistan"], cities=["Wah Cantt", "Taxila"])
    # First synthesized query is "clothing store in Wah Cantt" - already explicitly asked for.
    campaign.search_queries = ["clothing store in Wah Cantt"]

    from gtm_engine.storage.database import Database
    db = Database(settings.database_url)

    pipeline, StubWS = _build_pipeline(settings, defaults, db,
                                       geo_rows=[_mk_company("GeoOne")],
                                       fallback_rows=[_mk_company("Backfill")],
                                       monkeypatch=monkeypatch)

    await pipeline.discover(campaign)
    assert "clothing store in Wah Cantt" not in (StubWS.last_queries or [])
    assert "clothing store in Taxila" in (StubWS.last_queries or [])
