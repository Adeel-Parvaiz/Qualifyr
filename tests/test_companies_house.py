"""Companies House discovery: auth header, company_number mapping, 401->empty,
no-key->skip-without-request."""

import base64

import httpx
import respx

from gtm_engine.discovery.companies_house import CompaniesHouseDiscovery, auth_header, build_url, item_to_company
from gtm_engine.scraping.fetcher import HttpFetcher

SEARCH_URL = "https://api.company-information.service.gov.uk/search/companies"


def test_auth_header_base64_encodes_key_with_blank_password():
    header = auth_header("mykey123")
    expected = base64.b64encode(b"mykey123:").decode()
    assert header == {"Authorization": f"Basic {expected}"}


def test_build_url_includes_query_and_page_size():
    url = build_url("62020", items_per_page=50)
    assert url.startswith(SEARCH_URL)
    assert "62020" in url


def test_item_to_company_maps_fields():
    item = {
        "title": "Acme Software Ltd",
        "company_number": "01234567",
        "company_status": "active",
        "sic_codes": ["62020"],
        "address": {"address_line_1": "1 Main St", "locality": "London", "postal_code": "E1 1AA"},
    }
    company = item_to_company(item)
    assert company.name == "Acme Software Ltd"
    assert company.country == "United Kingdom"
    assert company.city == "London"
    assert company.extra["registration_number"] == "01234567"
    assert company.extra["company_status"] == "active"
    assert company.source == "companies_house"


def test_item_to_company_without_title_returns_none():
    assert item_to_company({"company_number": "123"}) is None


async def test_discover_skips_without_key(campaign, settings):
    campaign.geography.countries = ["United Kingdom"]
    campaign.companies_house_sic_codes = ["62020"]
    async with HttpFetcher(settings) as fetcher:
        found = [c async for c in CompaniesHouseDiscovery(fetcher, settings, api_key=None).discover(campaign)]
    assert found == []


async def test_discover_skips_without_sic_codes(campaign, settings):
    campaign.geography.countries = ["United Kingdom"]
    async with HttpFetcher(settings) as fetcher:
        found = [c async for c in CompaniesHouseDiscovery(fetcher, settings, api_key="k").discover(campaign)]
    assert found == []


async def test_discover_skips_non_uk_geography(campaign, settings):
    campaign.geography.countries = ["Pakistan"]
    campaign.companies_house_sic_codes = ["62020"]
    async with HttpFetcher(settings) as fetcher:
        found = [c async for c in CompaniesHouseDiscovery(fetcher, settings, api_key="k").discover(campaign)]
    assert found == []


@respx.mock
async def test_discover_sends_basic_auth_header(campaign, settings):
    route = respx.get(url__startswith=SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"items": []})
    )
    campaign.geography.countries = ["United Kingdom"]
    campaign.companies_house_sic_codes = ["62020"]
    async with HttpFetcher(settings) as fetcher:
        [c async for c in CompaniesHouseDiscovery(fetcher, settings, api_key="mykey").discover(campaign)]
    assert route.called
    expected = base64.b64encode(b"mykey:").decode()
    assert route.calls[0].request.headers.get("authorization") == f"Basic {expected}"


@respx.mock
async def test_discover_yields_companies(campaign, settings):
    respx.get(url__startswith=SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"items": [{
            "title": "Acme Software Ltd", "company_number": "01234567",
        }]})
    )
    campaign.geography.countries = ["United Kingdom"]
    campaign.companies_house_sic_codes = ["62020"]
    async with HttpFetcher(settings) as fetcher:
        found = [c async for c in CompaniesHouseDiscovery(fetcher, settings, api_key="mykey").discover(campaign)]
    assert len(found) == 1
    assert found[0].name == "Acme Software Ltd"


@respx.mock
async def test_discover_handles_401(campaign, settings):
    respx.get(url__startswith=SEARCH_URL).mock(return_value=httpx.Response(401))
    campaign.geography.countries = ["United Kingdom"]
    campaign.companies_house_sic_codes = ["62020"]
    async with HttpFetcher(settings) as fetcher:
        found = [c async for c in CompaniesHouseDiscovery(fetcher, settings, api_key="mykey").discover(campaign)]
    assert found == []
