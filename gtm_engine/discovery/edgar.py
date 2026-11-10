"""US SEC EDGAR discovery via the full-text search API. SEC mandates a descriptive
User-Agent on every request (https://www.sec.gov/os/webmaster-faq#developers) and actively
blocks generic/missing UAs.

Built on `efts.sec.gov/LATEST/search-index`, not the legacy `browse-edgar` SIC-filter atom
endpoint - that endpoint has a reproducible SEC-side bug where `<entry title="...">` and
`<company-info name="...">` render as the literal string "ARRAY(0x...)" instead of the real
company name (confirmed via direct curl, with and without a form-type filter). Full-text
search returns clean JSON and each hit already carries a `sics` list, so SIC-code filtering
is done client-side against that field instead."""

from __future__ import annotations

import json
import logging
from typing import AsyncIterator
from urllib.parse import urlencode

from gtm_engine.config.schema import CampaignConfig, EngineSettings
from gtm_engine.models import DiscoveredCompany
from gtm_engine.scraping.fetcher import HttpFetcher

log = logging.getLogger(__name__)

EDGAR_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"


def build_url(sic_code: str, forms: str = "10-K") -> str:
    params = {"q": f'"SIC {sic_code}"', "forms": forms}
    return f"{EDGAR_SEARCH_URL}?{urlencode(params)}"


def hit_to_company(hit: dict, sic_code: str) -> DiscoveredCompany | None:
    source = hit.get("_source") or {}
    sics = source.get("sics") or []
    if sic_code not in sics:
        return None
    names = source.get("display_names") or []
    if not names:
        return None
    # display_names look like "ACME CORP (CIK 0001234567) (Filer)"; strip the parenthetical tail.
    name = names[0].split(" (CIK")[0].strip()
    if not name:
        return None
    ciks = source.get("ciks") or []
    states = source.get("biz_states") or []
    return DiscoveredCompany(
        name=name,
        country="United States",
        city=(source.get("biz_locations") or [None])[0],
        category=f"sic={sic_code}",
        source="edgar",
        source_url=f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={ciks[0]}" if ciks else None,
        extra={
            "cik": ciks[0] if ciks else None,
            "sic": sic_code,
            "state": states[0] if states else None,
            "form": source.get("form"),
        },
    )


class EDGARDiscovery:
    name = "edgar"

    def __init__(self, fetcher: HttpFetcher, settings: EngineSettings):
        self.fetcher = fetcher
        self.settings = settings

    async def discover(self, campaign: CampaignConfig) -> AsyncIterator[DiscoveredCompany]:
        if not campaign.edgar_sic_codes:
            log.info("edgar: campaign has no edgar_sic_codes; skipping")
            return
        countries = {c.lower() for c in campaign.geography.countries}
        if countries and "united states" not in countries and "usa" not in countries and "us" not in countries:
            log.info("edgar: campaign geography does not include the US; skipping")
            return
        headers = {"User-Agent": self.settings.edgar_user_agent}
        matched = 0
        max_companies = self.settings.edgar_max_companies_per_run
        for sic_code in campaign.edgar_sic_codes:
            if matched >= max_companies:
                break
            url = build_url(sic_code)
            result = await self.fetcher.get(url, api=True, headers=headers)
            if not result.ok:
                log.warning("edgar: SIC %r failed (%s %s)", sic_code, result.status_code, result.error)
                continue
            try:
                payload = json.loads(result.text)
            except json.JSONDecodeError:
                log.warning("edgar: non-JSON response for SIC %r", sic_code)
                continue
            for hit in payload.get("hits", {}).get("hits", []):
                if matched >= max_companies:
                    break
                company = hit_to_company(hit, sic_code)
                if company:
                    matched += 1
                    yield company
        log.info("edgar: %d companies matched across %d SIC codes", matched, len(campaign.edgar_sic_codes))
