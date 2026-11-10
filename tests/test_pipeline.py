"""End-to-end: mocked Overpass + three fake websites -> qualified CSV holds only the
retailer, once, with evidence and a score reason. Mirrors the Phase-1 acceptance test."""

import csv
import json

import httpx
import respx

from conftest import fixture
from gtm_engine.discovery.geocode import NOMINATIM_URL
from gtm_engine.export.csv_export import write_csv
from gtm_engine.models import CSV_COLUMNS, CompanyType, Priority, SequenceStatus
from gtm_engine.pipeline import Pipeline
from gtm_engine.scraping.fetcher import HttpFetcher
from gtm_engine.storage.database import Database


class FakeMX:
    async def has_mx(self, domain: str) -> bool:
        return domain in {"zarafabrics.pk", "pixeldigital.pk"}


def _html(body: str) -> httpx.Response:
    return httpx.Response(200, text=body, headers={"content-type": "text/html; charset=utf-8"})


def _mock_world(settings):
    respx.get(url__startswith=NOMINATIM_URL).mock(
        return_value=httpx.Response(200, json=[{"boundingbox": ["33.5", "33.8", "72.8", "73.2"]}]))
    respx.get(url__startswith=settings.overpass_url).mock(
        return_value=httpx.Response(200, json=json.loads(fixture("overpass_islamabad.json"))))
    respx.get("https://www.zarafabrics.pk/").mock(return_value=_html(fixture("retailer_home.html")))
    respx.get("https://www.zarafabrics.pk/pages/about-us").mock(return_value=_html(fixture("retailer_about.html")))
    respx.get("https://www.zarafabrics.pk/pages/contact-us").mock(return_value=_html(fixture("retailer_contact.html")))
    respx.get("https://pixeldigital.pk/").mock(return_value=_html(fixture("agency_home.html")))
    respx.get("https://comingsoon-traders.pk/").mock(return_value=_html(fixture("thin_home.html")))
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(404))


@respx.mock
async def test_full_run_matches_acceptance_criteria(campaign, settings, defaults, tmp_path):
    _mock_world(settings)
    campaign.geography.cities = ["Islamabad"]
    db = Database(settings.database_url)
    async with HttpFetcher(settings) as fetcher:
        pipeline = Pipeline(settings, defaults, db, fetcher, mx=FakeMX())
        result = await pipeline.run(campaign)

    s = result.stats
    assert s.discovered == 5 and s.after_dedupe == 4  # two Zara nodes merged by domain
    assert s.processed == 4 and s.errors == 0
    assert s.vendor == 1 and s.buyer >= 1

    by_name = {l.company_name: l for l in result.leads}
    zara = by_name["Zara Fabrics"]
    assert zara.company_type == CompanyType.BUYER
    assert zara.priority != Priority.REJECT
    assert zara.total_score >= campaign.min_score
    assert zara.contact_name == "Ahmed Raza" and zara.contact_email == "ahmed.raza@zarafabrics.pk"
    assert zara.email_status.value == "mx_valid"
    assert zara.outreach_ready is True
    assert zara.score_reason and zara.buyer_fit_reason
    assert zara.source_url.startswith("https://www.openstreetmap.org/")
    assert "shopify" in zara.technologies
    assert "sells online" in zara.personalization_hook

    pixel = by_name["Pixel Digital"]
    assert pixel.company_type == CompanyType.VENDOR and pixel.priority == Priority.REJECT and not pixel.outreach_ready

    thin = by_name["Comingsoon Traders"]
    assert thin.company_type in (CompanyType.UNKNOWN, CompanyType.BUYER)  # category match may lift it
    assert not thin.outreach_ready  # no email -> never outreach

    # Unnamed Mobile Shop had only a facebook "website": no domain, search disabled -> no website
    unnamed = by_name["Unnamed Mobile Shop"]
    assert unnamed.website is None and s.no_website == 1
    assert unnamed.company_type == CompanyType.UNKNOWN  # category alone, nothing to verify

    # No duplicate domains in output
    domains = [l.domain for l in result.leads if l.domain]
    assert len(domains) == len(set(domains))

    # CSV: exact columns, only qualified buyers
    qualified = [l for l in result.leads if l.company_type == CompanyType.BUYER and l.total_score >= campaign.min_score]
    out = write_csv(qualified, tmp_path / "q.csv")
    with out.open(encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    assert list(rows[0].keys()) == CSV_COLUMNS
    assert {r["company_name"] for r in rows} == {"Zara Fabrics"}
    assert rows[0]["outreach_ready"] == "true"

    # Stored and queryable
    stored = db.list_leads(campaign.campaign_id, min_score=campaign.min_score, company_type="BUYER")
    assert [l.company_name for l in stored] == ["Zara Fabrics"]
    assert db.get_run(result.run_id)["status"] == "completed"
    db.close()


@respx.mock
async def test_rerun_updates_lead_in_place_and_respects_suppression(campaign, settings, defaults):
    _mock_world(settings)
    campaign.geography.cities = ["Islamabad"]
    db = Database(settings.database_url)
    async with HttpFetcher(settings) as fetcher:
        pipeline = Pipeline(settings, defaults, db, fetcher, mx=FakeMX())
        first = await pipeline.run(campaign)
        db.add_suppression("zarafabrics.pk", "domain", "asked not to be contacted")
        second = await pipeline.run(campaign)

    z1 = next(l for l in first.leads if l.company_name == "Zara Fabrics")
    z2 = next(l for l in second.leads if l.company_name == "Zara Fabrics")
    assert z1.lead_id == z2.lead_id
    assert z1.outreach_ready and not z2.outreach_ready
    assert z2.sequence_status == SequenceStatus.SUPPRESSED
    assert len(db.list_leads(campaign.campaign_id)) == len(second.leads)  # no duplicate rows
    db.close()


@respx.mock
async def test_new_leads_counter_only_counts_genuine_first_sightings(campaign, settings, defaults):
    """RunStats.new_leads is what the campaign card's "+N new leads" badge reads. It must only
    count leads that are actually useful - qualified or outreach-ready - not every company
    record that got parsed, or the badge would claim "new leads" when most are junk the
    pipeline correctly filtered out. A rerun over the same companies should count zero."""
    _mock_world(settings)
    campaign.geography.cities = ["Islamabad"]
    db = Database(settings.database_url)
    async with HttpFetcher(settings) as fetcher:
        pipeline = Pipeline(settings, defaults, db, fetcher, mx=FakeMX())
        first = await pipeline.run(campaign)
        second = await pipeline.run(campaign)

    useful_first = [l for l in first.leads if l.outreach_ready or
                    (l.company_type == CompanyType.BUYER and l.total_score >= campaign.min_score)]
    assert first.stats.new_leads == len(useful_first)
    assert first.stats.new_leads > 0
    assert first.stats.new_leads < len(first.leads), (
        "fixture must include a non-qualifying lead for this test to actually exercise the filter")
    assert second.stats.new_leads == 0
    db.close()


@respx.mock
async def test_search_found_domain_that_duplicates_earlier_company_is_skipped(campaign, settings, defaults):
    """'Super Store' has no OSM website; search resolves it to the same domain as 'Superstore'."""
    settings.enable_search_fallback = True
    respx.get(url__startswith="https://html.duckduckgo.com/html/").mock(return_value=httpx.Response(
        200, text='<a class="result__a" href="https://www.zarafabrics.pk/">Zara Fabrics</a>'))
    _mock_world(settings)  # registers the 404 catch-all, so it must come after the search mock
    campaign.geography.cities = ["Islamabad"]
    campaign.osm_categories = []
    campaign.seed_csv = None
    db = Database(settings.database_url)
    from gtm_engine.models import DiscoveredCompany

    async def fake_discover(_campaign, _progress=None):
        return [
            DiscoveredCompany(name="Zara Fabrics", website="https://www.zarafabrics.pk", city="Islamabad", country="Pakistan", source="osm"),
            DiscoveredCompany(name="Zara Fabrics Store", city="Islamabad", country="Pakistan", source="osm"),
        ]

    async with HttpFetcher(settings) as fetcher:
        pipeline = Pipeline(settings, defaults, db, fetcher, mx=FakeMX())
        pipeline.discover = fake_discover
        result = await pipeline.run(campaign)
    assert result.stats.duplicates == 1
    assert [l.company_name for l in result.leads] == ["Zara Fabrics"]
    db.close()


@respx.mock
async def test_exclude_chains_drops_branded_outlets(campaign, settings, defaults):
    _mock_world(settings)
    campaign.geography.cities = ["Islamabad"]
    campaign.exclude_chains = True
    campaign.osm_categories = []
    db = Database(settings.database_url)
    from gtm_engine.models import DiscoveredCompany

    async def fake_discover(_c, _p=None):
        return [
            DiscoveredCompany(name="Bata", website="https://www.zarafabrics.pk", city="Islamabad", source="osm", extra={"brand": "Bata"}),
            DiscoveredCompany(name="Zara Fabrics", website="https://www.zarafabrics.pk", city="Islamabad", source="osm"),
        ]

    async with HttpFetcher(settings) as fetcher:
        pipeline = Pipeline(settings, defaults, db, fetcher, mx=FakeMX())
        pipeline.discover = fake_discover
        result = await pipeline.run(campaign)
    assert result.stats.chains_excluded == 1 and [l.company_name for l in result.leads] == ["Zara Fabrics"]
    db.close()


class FakeAliveResolver:
    """Always resolves live, so the guaranteed-floor tests exercise the expansion loop's
    own logic without depending on real DNS."""

    async def resolves(self, url):
        return True


@respx.mock
async def test_expansion_disabled_when_min_outreach_ready_is_zero(campaign, settings, defaults):
    """0 is the explicit opt-out: behaves exactly like today's single-pass run."""
    _mock_world(settings)
    campaign.geography.cities = ["Islamabad"]
    campaign.max_companies = 1
    campaign.min_outreach_ready = 0
    db = Database(settings.database_url)
    from gtm_engine.models import DiscoveredCompany

    async def fake_discover(_c, _p=None):
        return [
            DiscoveredCompany(name="Zara Fabrics", website="https://www.zarafabrics.pk/",
                              city="Islamabad", country="Pakistan", source="osm"),
            DiscoveredCompany(name="Pixel Digital", website="https://pixeldigital.pk",
                              city="Islamabad", country="Pakistan", source="osm"),
        ]

    async with HttpFetcher(settings) as fetcher:
        pipeline = Pipeline(settings, defaults, db, fetcher, mx=FakeMX(), resolver=FakeAliveResolver())
        pipeline.discover = fake_discover
        result = await pipeline.run(campaign)
    assert result.stats.processed == 1
    assert result.stats.expansion_rounds == 0
    assert result.stats.target_met is True
    db.close()


@respx.mock
async def test_expansion_stops_once_floor_is_met(campaign, settings, defaults):
    """Zara alone is outreach-ready; a floor of 1 is met in round one without touching
    the rest of the pool."""
    _mock_world(settings)
    campaign.geography.cities = ["Islamabad"]
    campaign.max_companies = 1
    campaign.min_outreach_ready = 1
    db = Database(settings.database_url)
    from gtm_engine.models import DiscoveredCompany

    async def fake_discover(_c, _p=None):
        return [
            DiscoveredCompany(name="Zara Fabrics", website="https://www.zarafabrics.pk/",
                              city="Islamabad", country="Pakistan", source="osm"),
            DiscoveredCompany(name="Pixel Digital", website="https://pixeldigital.pk",
                              city="Islamabad", country="Pakistan", source="osm"),
        ]

    async with HttpFetcher(settings) as fetcher:
        pipeline = Pipeline(settings, defaults, db, fetcher, mx=FakeMX(), resolver=FakeAliveResolver())
        pipeline.discover = fake_discover
        result = await pipeline.run(campaign)
    assert result.stats.outreach_ready == 1
    assert result.stats.expansion_rounds == 0
    assert result.stats.target_met is True
    assert result.stats.processed == 1  # Pixel Digital never touched - floor already met
    db.close()


@respx.mock
async def test_expansion_pulls_more_when_floor_is_not_met(campaign, settings, defaults):
    """Zara is the only outreach-ready company in this world; a floor of 2 can never be
    met, so expansion should consume the rest of the pool and report the shortfall
    honestly instead of silently returning fewer leads than promised."""
    _mock_world(settings)
    campaign.geography.cities = ["Islamabad"]
    # P3 (2026-10-10): max_companies is now the user's lead target itself, and
    # min_outreach_ready is capped at it — so the floor here must be <= max_companies
    # for this test to still exercise "floor requested but unreachable" rather than
    # the newer "floor silently capped down to the target" path.
    campaign.max_companies = 2
    campaign.min_outreach_ready = 2
    db = Database(settings.database_url)
    from gtm_engine.models import DiscoveredCompany

    async def fake_discover(_c, _p=None):
        return [
            DiscoveredCompany(name="Zara Fabrics", website="https://www.zarafabrics.pk/",
                              city="Islamabad", country="Pakistan", source="osm"),
            DiscoveredCompany(name="Pixel Digital", website="https://pixeldigital.pk",
                              city="Islamabad", country="Pakistan", source="osm"),
            DiscoveredCompany(name="Comingsoon Traders", website="https://comingsoon-traders.pk",
                              city="Islamabad", country="Pakistan", source="osm"),
        ]

    async with HttpFetcher(settings) as fetcher:
        pipeline = Pipeline(settings, defaults, db, fetcher, mx=FakeMX(), resolver=FakeAliveResolver())
        pipeline.discover = fake_discover
        result = await pipeline.run(campaign)
    assert result.stats.outreach_ready == 1  # only Zara ever qualifies+has a contact
    assert result.stats.processed == 3       # the whole pool got consumed trying to reach 2
    assert result.stats.expansion_rounds >= 1
    assert result.stats.target_met is False   # honest shortfall, not silently under-delivered
    db.close()
