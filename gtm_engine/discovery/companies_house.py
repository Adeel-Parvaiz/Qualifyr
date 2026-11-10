"""UK Companies House discovery: free self-serve API key, Basic Auth (key as username,
blank password). `company_number` maps into `extra["registration_number"]`, the entity
resolution tier right below LEI for merging the same company across sources.

The public `/search/companies` endpoint only supports a free-text `q` term (company name
or number) - there is no native SIC-code filter on search. `companies_house_sic_codes` is
therefore used as the search term itself (SIC codes do appear in company profile text indexed
by the search), which is a best-effort match, not an exact filter. An exact-filter design would
need the Advanced Search endpoint, which requires a different auth scope; out of scope here."""

from __future__ import annotations

import base64
import json
import logging
from typing import AsyncIterator
from urllib.parse import urlencode

from gtm_engine.config.schema import CampaignConfig, EngineSettings
from gtm_engine.models import DiscoveredCompany
from gtm_engine.scraping.fetcher import HttpFetcher

log = logging.getLogger(__name__)

SEARCH_URL = "https://api.company-information.service.gov.uk/search/companies"


def build_url(query: str, items_per_page: int) -> str:
    return f"{SEARCH_URL}?{urlencode({'q': query, 'items_per_page': items_per_page})}"


def auth_header(api_key: str) -> dict[str, str]:
    token = base64.b64encode(f"{api_key}:".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def item_to_company(item: dict) -> DiscoveredCompany | None:
    title = item.get("title")
    if not title:
        return None
    address = item.get("address") or {}
    parts = [address.get("address_line_1"), address.get("address_line_2"),
             address.get("locality"), address.get("postal_code")]
    return DiscoveredCompany(
        name=title.strip(),
        country="United Kingdom",
        city=address.get("locality"),
        address=", ".join(p for p in parts if p) or None,
        category=f"sic={item.get('sic_codes')[0]}" if item.get("sic_codes") else "registry=companies_house",
        source="companies_house",
        source_url=f"https://find-and-update.company-information.service.gov.uk/company/{item.get('company_number')}"
                   if item.get("company_number") else None,
        extra={
            "registration_number": item.get("company_number"),
            "company_status": item.get("company_status"),
            "sic_codes": item.get("sic_codes"),
        },
    )


class CompaniesHouseDiscovery:
    name = "companies_house"

    def __init__(self, fetcher: HttpFetcher, settings: EngineSettings, api_key: str | None):
        self.fetcher = fetcher
        self.settings = settings
        self.api_key = api_key

    async def discover(self, campaign: CampaignConfig) -> AsyncIterator[DiscoveredCompany]:
        if not self.api_key:
            log.warning("companies_house: no API key resolved; skipping")
            return
        if not campaign.companies_house_sic_codes:
            log.info("companies_house: campaign has no companies_house_sic_codes; skipping")
            return
        countries = {c.lower() for c in campaign.geography.countries}
        if countries and not (countries & {"united kingdom", "uk", "gb", "great britain"}):
            log.info("companies_house: campaign geography does not include the UK; skipping")
            return
        headers = auth_header(self.api_key)
        matched = 0
        max_companies = self.settings.companies_house_max_companies_per_run
        for sic_code in campaign.companies_house_sic_codes:
            if matched >= max_companies:
                break
            url = build_url(sic_code, items_per_page=min(100, max_companies))
            result = await self.fetcher.get(url, api=True, headers=headers)
            if not result.ok:
                log.warning("companies_house: SIC %r failed (%s %s)", sic_code, result.status_code, result.error)
                continue
            try:
                payload = json.loads(result.text)
            except json.JSONDecodeError:
                log.warning("companies_house: non-JSON response for SIC %r", sic_code)
                continue
            for item in payload.get("items", []):
                if matched >= max_companies:
                    break
                company = item_to_company(item)
                if company:
                    matched += 1
                    yield company
        log.info("companies_house: %d companies matched across %d SIC codes",
                  matched, len(campaign.companies_house_sic_codes))
