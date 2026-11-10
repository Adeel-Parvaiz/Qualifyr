"""Wikidata SPARQL discovery: free, keyless, but requires a descriptive custom User-Agent
per Wikidata's etiquette (https://meta.wikimedia.org/wiki/User-Agent_policy) - a generic or
missing UA risks being rate-limited or blocked."""

from __future__ import annotations

import json
import logging
from typing import AsyncIterator
from urllib.parse import urlencode

from gtm_engine.config.schema import CampaignConfig, EngineSettings
from gtm_engine.models import DiscoveredCompany
from gtm_engine.scraping.fetcher import HttpFetcher

log = logging.getLogger(__name__)

WIKIDATA_URL = "https://query.wikidata.org/sparql"


def build_query(industry_label: str, country_code: str | None, limit: int) -> str:
    """P31 instance-of business (Q4830453), optional P17 country, P452 industry label match,
    P856 website. Country is matched via Wikidata's own English country label when a
    country_code hint isn't resolvable to a QID without a second lookup, so we filter on the
    country's label text instead - simpler than a QID join for a free-tier source."""
    country_filter = ""
    if country_code:
        country_filter = f'?item wdt:P17 ?country . ?country wdt:P298 "{country_code}" .'
    return f"""
    SELECT ?item ?itemLabel ?website ?industryLabel WHERE {{
      ?item wdt:P31 wd:Q4830453 .
      ?item wdt:P452 ?industry .
      ?industry rdfs:label ?industryLabel .
      FILTER(LANG(?industryLabel) = "en")
      FILTER(CONTAINS(LCASE(?industryLabel), "{industry_label.lower()}"))
      OPTIONAL {{ ?item wdt:P856 ?website . }}
      {country_filter}
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
    }}
    LIMIT {limit}
    """


def build_url(query: str) -> str:
    return f"{WIKIDATA_URL}?{urlencode({'query': query, 'format': 'json'})}"


def binding_to_company(binding: dict, fallback_country: str | None) -> DiscoveredCompany | None:
    label = (binding.get("itemLabel") or {}).get("value")
    if not label:
        return None
    item_uri = (binding.get("item") or {}).get("value")
    qid = item_uri.rsplit("/", 1)[-1] if item_uri else None
    return DiscoveredCompany(
        name=label.strip(),
        website=(binding.get("website") or {}).get("value"),
        country=fallback_country,
        category="registry=wikidata",
        source="wikidata",
        source_url=item_uri,
        extra={"wikidata_qid": qid, "industry_label": (binding.get("industryLabel") or {}).get("value")},
    )


class WikidataDiscovery:
    name = "wikidata"

    def __init__(self, fetcher: HttpFetcher, settings: EngineSettings):
        self.fetcher = fetcher
        self.settings = settings

    async def discover(self, campaign: CampaignConfig) -> AsyncIterator[DiscoveredCompany]:
        if not campaign.wikidata_industries:
            log.info("wikidata: campaign has no wikidata_industries; skipping")
            return
        fallback_country = campaign.geography.countries[0] if campaign.geography.countries else None
        country_code = campaign.geography.country_codes[0] if campaign.geography.country_codes else None
        industries = campaign.wikidata_industries[: self.settings.wikidata_max_queries_per_run]
        headers = {"User-Agent": self.settings.user_agent}
        matched = 0
        for industry in industries:
            query = build_query(industry, country_code, limit=50)
            url = build_url(query)
            result = await self.fetcher.get(url, api=True, headers=headers)
            if not result.ok:
                log.warning("wikidata: query %r failed (%s %s)", industry, result.status_code, result.error)
                continue
            try:
                payload = json.loads(result.text)
            except json.JSONDecodeError:
                log.warning("wikidata: non-JSON response for query %r", industry)
                continue
            for binding in payload.get("results", {}).get("bindings", []):
                company = binding_to_company(binding, fallback_country)
                if company:
                    matched += 1
                    yield company
        log.info("wikidata: %d records matched across %d industries", matched, len(industries))
