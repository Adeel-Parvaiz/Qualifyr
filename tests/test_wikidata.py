"""Wikidata discovery: SPARQL construction, binding->company mapping, custom UA, failure->empty."""

import httpx
import respx

from gtm_engine.discovery.wikidata import WikidataDiscovery, binding_to_company, build_query, build_url
from gtm_engine.scraping.fetcher import HttpFetcher

WIKIDATA_URL = "https://query.wikidata.org/sparql"


def test_build_query_filters_by_industry_and_country():
    q = build_query("textiles", "PK", limit=50)
    assert "textiles" in q
    assert 'wdt:P298 "PK"' in q
    assert "LIMIT 50" in q


def test_build_query_without_country_omits_filter():
    q = build_query("textiles", None, limit=50)
    assert "P298" not in q


def test_binding_to_company_maps_fields():
    binding = {
        "item": {"value": "http://www.wikidata.org/entity/Q12345"},
        "itemLabel": {"value": "Acme Corp"},
        "website": {"value": "https://acme.example"},
        "industryLabel": {"value": "textile manufacturing"},
    }
    company = binding_to_company(binding, fallback_country="Pakistan")
    assert company.name == "Acme Corp"
    assert company.website == "https://acme.example"
    assert company.country == "Pakistan"
    assert company.extra["wikidata_qid"] == "Q12345"
    assert company.source == "wikidata"


def test_binding_without_label_returns_none():
    assert binding_to_company({}, fallback_country=None) is None


@respx.mock
async def test_discover_sends_custom_user_agent(campaign, settings):
    route = respx.get(url__startswith=WIKIDATA_URL).mock(
        return_value=httpx.Response(200, json={"results": {"bindings": []}})
    )
    campaign.wikidata_industries = ["textiles"]
    async with HttpFetcher(settings) as fetcher:
        [c async for c in WikidataDiscovery(fetcher, settings).discover(campaign)]
    assert route.called
    sent_ua = route.calls[0].request.headers.get("user-agent")
    assert sent_ua == settings.user_agent


@respx.mock
async def test_discover_yields_companies(campaign, settings):
    respx.get(url__startswith=WIKIDATA_URL).mock(
        return_value=httpx.Response(200, json={"results": {"bindings": [{
            "item": {"value": "http://www.wikidata.org/entity/Q1"},
            "itemLabel": {"value": "Acme Corp"},
        }]}})
    )
    campaign.wikidata_industries = ["textiles"]
    async with HttpFetcher(settings) as fetcher:
        found = [c async for c in WikidataDiscovery(fetcher, settings).discover(campaign)]
    assert len(found) == 1
    assert found[0].name == "Acme Corp"


async def test_discover_skips_without_industries(campaign, settings):
    async with HttpFetcher(settings) as fetcher:
        found = [c async for c in WikidataDiscovery(fetcher, settings).discover(campaign)]
    assert found == []


@respx.mock
async def test_discover_handles_fetch_failure(campaign, settings):
    respx.get(url__startswith=WIKIDATA_URL).mock(return_value=httpx.Response(500))
    campaign.wikidata_industries = ["textiles"]
    async with HttpFetcher(settings) as fetcher:
        found = [c async for c in WikidataDiscovery(fetcher, settings).discover(campaign)]
    assert found == []
