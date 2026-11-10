"""EDGAR discovery: URL construction, hit->company mapping (incl. SIC-code gate), custom UA,
geography gate, failure->empty."""

import httpx
import respx

from gtm_engine.discovery.edgar import EDGARDiscovery, build_url, hit_to_company
from gtm_engine.scraping.fetcher import HttpFetcher

EDGAR_URL = "https://efts.sec.gov/LATEST/search-index"


def test_build_url_includes_sic_and_forms():
    url = build_url("7372")
    assert url.startswith(EDGAR_URL)
    assert "7372" in url
    assert "10-K" in url


def test_hit_to_company_maps_fields():
    hit = {"_source": {
        "sics": ["7372"],
        "display_names": ["ACME SOFTWARE CORP (CIK 0001234567) (Filer)"],
        "ciks": ["0001234567"],
        "biz_locations": ["Boston, MA"],
        "biz_states": ["MA"],
        "form": "10-K",
    }}
    company = hit_to_company(hit, "7372")
    assert company.name == "ACME SOFTWARE CORP"
    assert company.country == "United States"
    assert company.city == "Boston, MA"
    assert company.extra["cik"] == "0001234567"
    assert company.extra["sic"] == "7372"
    assert company.extra["state"] == "MA"


def test_hit_to_company_wrong_sic_returns_none():
    hit = {"_source": {"sics": ["9999"], "display_names": ["ACME CORP"]}}
    assert hit_to_company(hit, "7372") is None


def test_hit_to_company_without_name_returns_none():
    hit = {"_source": {"sics": ["7372"], "display_names": []}}
    assert hit_to_company(hit, "7372") is None


@respx.mock
async def test_discover_sends_custom_user_agent(campaign, settings):
    route = respx.get(url__startswith=EDGAR_URL).mock(
        return_value=httpx.Response(200, json={"hits": {"hits": []}})
    )
    campaign.geography.countries = ["United States"]
    campaign.edgar_sic_codes = ["7372"]
    async with HttpFetcher(settings) as fetcher:
        [c async for c in EDGARDiscovery(fetcher, settings).discover(campaign)]
    assert route.called
    assert route.calls[0].request.headers.get("user-agent") == settings.edgar_user_agent


@respx.mock
async def test_discover_yields_companies(campaign, settings):
    respx.get(url__startswith=EDGAR_URL).mock(
        return_value=httpx.Response(200, json={"hits": {"hits": [{"_source": {
            "sics": ["7372"],
            "display_names": ["ACME SOFTWARE CORP (CIK 0001234567) (Filer)"],
            "ciks": ["0001234567"],
        }}]}})
    )
    campaign.geography.countries = ["United States"]
    campaign.edgar_sic_codes = ["7372"]
    async with HttpFetcher(settings) as fetcher:
        found = [c async for c in EDGARDiscovery(fetcher, settings).discover(campaign)]
    assert len(found) == 1
    assert found[0].name == "ACME SOFTWARE CORP"


async def test_discover_skips_without_sic_codes(campaign, settings):
    campaign.geography.countries = ["United States"]
    async with HttpFetcher(settings) as fetcher:
        found = [c async for c in EDGARDiscovery(fetcher, settings).discover(campaign)]
    assert found == []


async def test_discover_skips_non_us_geography(campaign, settings):
    campaign.geography.countries = ["Pakistan"]
    campaign.edgar_sic_codes = ["7372"]
    async with HttpFetcher(settings) as fetcher:
        found = [c async for c in EDGARDiscovery(fetcher, settings).discover(campaign)]
    assert found == []


@respx.mock
async def test_discover_handles_fetch_failure(campaign, settings):
    respx.get(url__startswith=EDGAR_URL).mock(return_value=httpx.Response(500))
    campaign.geography.countries = ["United States"]
    campaign.edgar_sic_codes = ["7372"]
    async with HttpFetcher(settings) as fetcher:
        found = [c async for c in EDGARDiscovery(fetcher, settings).discover(campaign)]
    assert found == []
