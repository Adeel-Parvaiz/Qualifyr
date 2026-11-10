"""GLEIF discovery: URL construction, record->company mapping, failure->empty."""

import httpx
import respx

from gtm_engine.discovery.gleif import GLEIFDiscovery, build_url, record_to_company
from gtm_engine.scraping.fetcher import HttpFetcher

GLEIF_URL = "https://api.gleif.org/api/v1/lei-records"


def test_build_url_encodes_query():
    url = build_url("Acme Corp", page_size=20)
    assert url.startswith(GLEIF_URL)
    assert "Acme" in url
    assert "page" in url and "size" in url


def test_record_to_company_maps_fields():
    record = {
        "attributes": {
            "lei": "LEI123",
            "registration": {"status": "ISSUED"},
            "entity": {
                "legalName": {"name": "Acme Corp"},
                "legalAddress": {"city": "Karachi", "country": "PK", "addressLines": ["1 Main St"]},
                "jurisdiction": "PK",
                "status": "ACTIVE",
            },
        }
    }
    company = record_to_company(record, fallback_country="Pakistan")
    assert company.name == "Acme Corp"
    assert company.city == "Karachi"
    assert company.country == "PK"
    assert company.address == "1 Main St"
    assert company.extra["lei"] == "LEI123"
    assert company.extra["jurisdiction"] == "PK"
    assert company.extra["registration_status"] == "ISSUED"


def test_record_to_company_without_legal_name_returns_none():
    assert record_to_company({"attributes": {"entity": {}}}, fallback_country=None) is None


@respx.mock
async def test_discover_yields_companies(campaign, settings):
    respx.get(url__startswith=GLEIF_URL).mock(
        return_value=httpx.Response(200, json={"data": [{
            "attributes": {"lei": "LEI1", "entity": {"legalName": {"name": "Acme Ltd"}}},
        }]})
    )
    campaign.gleif_lei_queries = ["Acme"]
    async with HttpFetcher(settings) as fetcher:
        found = [c async for c in GLEIFDiscovery(fetcher, settings).discover(campaign)]
    assert len(found) == 1
    assert found[0].name == "Acme Ltd"
    assert found[0].source == "gleif"


async def test_discover_skips_without_queries(campaign, settings):
    async with HttpFetcher(settings) as fetcher:
        found = [c async for c in GLEIFDiscovery(fetcher, settings).discover(campaign)]
    assert found == []


@respx.mock
async def test_discover_handles_fetch_failure(campaign, settings):
    respx.get(url__startswith=GLEIF_URL).mock(return_value=httpx.Response(500))
    campaign.gleif_lei_queries = ["Acme"]
    async with HttpFetcher(settings) as fetcher:
        found = [c async for c in GLEIFDiscovery(fetcher, settings).discover(campaign)]
    assert found == []
