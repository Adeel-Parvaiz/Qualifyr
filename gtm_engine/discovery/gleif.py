"""GLEIF LEI-record search: a free, keyless API that returns the entity's Legal Entity
Identifier (LEI) – the strongest tier entity resolution has for merging the same company
found under different name strings across sources."""

from __future__ import annotations

import json
import logging
from typing import AsyncIterator
from urllib.parse import urlencode

from gtm_engine.config.schema import CampaignConfig, EngineSettings
from gtm_engine.models import DiscoveredCompany
from gtm_engine.scraping.fetcher import HttpFetcher

log = logging.getLogger(__name__)

GLEIF_URL = "https://api.gleif.org/api/v1/lei-records"


def build_url(query: str, page_size: int) -> str:
    params = {"filter[entity.legalName]": query, "page[size]": page_size}
    return f"{GLEIF_URL}?{urlencode(params)}"


def record_to_company(record: dict, fallback_country: str | None) -> DiscoveredCompany | None:
    attrs = record.get("attributes") or {}
    entity = attrs.get("entity") or {}
    legal_name = (entity.get("legalName") or {}).get("name")
    if not legal_name:
        return None
    address = entity.get("legalAddress") or {}
    lines = address.get("addressLines") or []
    return DiscoveredCompany(
        name=legal_name.strip(),
        country=address.get("country") or fallback_country,
        city=address.get("city"),
        address=", ".join(l for l in lines if l) or None,
        category="registry=gleif",
        source="gleif",
        source_url=f"https://search.gleif.org/#/record/{attrs.get('lei')}" if attrs.get("lei") else None,
        extra={
            "lei": attrs.get("lei"),
            "jurisdiction": entity.get("jurisdiction"),
            "registration_status": (attrs.get("registration") or {}).get("status"),
            "entity_status": entity.get("status"),
        },
    )


class GLEIFDiscovery:
    name = "gleif"

    def __init__(self, fetcher: HttpFetcher, settings: EngineSettings):
        self.fetcher = fetcher
        self.settings = settings

    async def discover(self, campaign: CampaignConfig) -> AsyncIterator[DiscoveredCompany]:
        if not campaign.gleif_lei_queries:
            log.info("gleif: campaign has no gleif_lei_queries; skipping")
            return
        fallback_country = campaign.geography.countries[0] if campaign.geography.countries else None
        queries = campaign.gleif_lei_queries[: self.settings.gleif_max_queries_per_run]
        matched = 0
        for query in queries:
            url = build_url(query, page_size=20)
            result = await self.fetcher.get(url, api=True)
            if not result.ok:
                log.warning("gleif: query %r failed (%s %s)", query, result.status_code, result.error)
                continue
            try:
                payload = json.loads(result.text)
            except json.JSONDecodeError:
                log.warning("gleif: non-JSON response for query %r", query)
                continue
            for record in payload.get("data", []):
                company = record_to_company(record, fallback_country)
                if company:
                    matched += 1
                    yield company
        log.info("gleif: %d records matched across %d queries", matched, len(queries))
