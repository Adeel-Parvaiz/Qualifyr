"""Web-search discovery (E2): find companies by what they DO, not just mapped storefronts.

Runs the offer's derived search queries through a web search (Brave, or keyless DuckDuckGo),
takes each result's own domain, drops directories/aggregators/social, and yields it as a
company for the pipeline to crawl, classify and judge. This is what lets an offer reach
software firms, service businesses and online-only brands that carry no OSM/Overture tag.

Honest by construction: it asserts only the domain (a real site the search returned) and a
name guessed from that domain; everything else the pipeline learns by crawling. The buyer/
vendor gate and the intent judge filter out competitors and non-buyers downstream."""

from __future__ import annotations

import logging
import re
from typing import AsyncIterator

from gtm_engine.config.schema import CampaignConfig, EngineSettings
from gtm_engine.discovery.search import _DIRECTORY_DOMAINS, search_web
from gtm_engine.models import DiscoveredCompany
from gtm_engine.scraping.fetcher import HttpFetcher
from gtm_engine.validation.domains import canonical_domain, is_social_url

log = logging.getLogger(__name__)


def _name_from_domain(domain: str) -> str:
    """A readable name guess from the domain label: `acme-textiles.pk` -> `Acme Textiles`. The
    real name is re-derived from the homepage during crawling; this is just a placeholder."""
    label = domain.split(".")[0]
    return re.sub(r"[-_]+", " ", label).strip().title() or domain


class WebSearchDiscovery:
    name = "websearch"

    def __init__(self, fetcher: HttpFetcher, settings: EngineSettings,
                 *, brave_api_key: str | None = None):
        self.fetcher = fetcher
        self.settings = settings
        self.brave_api_key = brave_api_key

    async def discover(self, campaign: CampaignConfig) -> AsyncIterator[DiscoveredCompany]:
        queries = campaign.search_queries[: self.settings.web_search_max_queries_per_run]
        country = (campaign.geography.countries or [None])[0]
        seen: set[str] = set()
        for query in queries:
            for url, title in await search_web(self.fetcher, self.settings, query,
                                               brave_api_key=self.brave_api_key):
                domain = canonical_domain(url)
                if not domain or domain in seen or is_social_url(url) or domain in _DIRECTORY_DOMAINS:
                    continue
                seen.add(domain)
                yield DiscoveredCompany(
                    name=_name_from_domain(domain),
                    website=f"https://{domain}",
                    domain=domain,
                    country=country,
                    source="websearch",
                    source_url=url,
                    extra={"query": query, "result_title": title},
                )
