"""Search-engine fallback: find a company's own website when the discovery source did
not carry one. Conservative by design: a result is accepted only when the company name
clearly appears in the result domain or title. Wrong websites are worse than none."""

from __future__ import annotations

import json
import logging
import os
import re
from urllib.parse import parse_qs, quote_plus, urlparse

from bs4 import BeautifulSoup

from gtm_engine.config.schema import EngineSettings
from gtm_engine.scraping.fetcher import HttpFetcher
from gtm_engine.validation.dedupe import normalize_name
from gtm_engine.validation.domains import canonical_domain, is_social_url

log = logging.getLogger(__name__)

SEARCH_URL = "https://html.duckduckgo.com/html/?q={q}"
# Note: the free Brave plan rejects the `country` parameter (422), so the query carries
# the country instead of filtering by it.
BRAVE_URL = "https://api.search.brave.com/res/v1/web/search?q={q}&count=8"

# Aggregators/directories that rank for any business name but are never its own site.
_DIRECTORY_DOMAINS = {
    "facebook.com", "instagram.com", "linkedin.com", "yelp.com", "foursquare.com",
    "tripadvisor.com", "yellowpages.com", "yellowpages.pk", "wikipedia.org", "wikidata.org",
    "daraz.pk", "olx.com.pk", "zameen.com", "rozee.pk", "indeed.com", "glassdoor.com",
    "pakbiz.com", "businesslist.pk", "pk.locanto.asia", "findpk.com", "cybo.com", "hipages.com",
    "mapquest.com", "trustpilot.com", "crunchbase.com", "zoominfo.com", "dnb.com", "apollo.io",
    "youtube.com", "tiktok.com", "twitter.com", "x.com", "pinterest.com", "amazon.com",
    "alibaba.com", "aliexpress.com", "justdial.com", "sulekha.com", "businessbook.pk", "pakistanyp.com",
    "yellowpages.com.pk", "pakbiz.com", "tradekey.com", "exportersindia.com", "kompass.com", "hotfrog.com",
}

_STOPWORDS = {"the", "and", "of", "pvt", "ltd", "limited", "private", "company", "co", "store",
              "shop", "outlet", "pakistan", "karachi", "lahore", "islamabad", "rawalpindi"}


def _tokens(text: str) -> set[str]:
    return {t for t in normalize_name(text).split() if len(t) > 2 and t not in _STOPWORDS}


_GENERIC_TOKENS = {"electronics", "fashion", "furniture", "mobile", "mobiles", "garments", "traders",
                   "trading", "enterprises", "international", "brothers", "sons", "mart", "super",
                   "collection", "collections", "boutique", "clinic", "hospital", "school", "pharmacy"}


def name_matches(company_name: str, domain: str, title: str | None) -> bool:
    """True only when the company name is clearly reflected in the domain or page title."""
    toks = _tokens(company_name)
    if not toks:
        return False
    label = domain.split(".")[0]
    distinctive = {t for t in toks if t not in _GENERIC_TOKENS}
    ordered = [t for t in normalize_name(company_name).split() if t in toks]  # name order, deterministic
    squashed_name = re.sub(r"[^a-z0-9]", "", company_name.lower())

    # 1. The whole name is the label: "csd" -> csd.gov.pk, "savemart" -> savemartonline.pk
    joined = "".join(ordered)
    if label == joined or (joined in label and (distinctive or len(joined) >= 8)):
        return True
    # 2. The label sits inside the name: "electricstore" in "ElectricStorePk Electric Store".
    #    A generic label ("electronics", "mobile") proves nothing on its own.
    if len(label) >= 5 and label not in _GENERIC_TOKENS and label in squashed_name:
        if not distinctive or any(d in label for d in distinctive):
            return True
    # 3. A distinctive token of the name is in the label: "fatah" in alfatah.pk
    if any(len(t) >= 5 and t in label for t in distinctive):
        return True
    # 4. The homepage title carries (nearly) the whole name, including a distinctive part.
    if title:
        overlap = toks & _tokens(title)
        needed = len(toks) - (1 if len(toks) > 2 else 0)
        if len(overlap) >= max(1, needed) and (not distinctive or overlap & distinctive):
            return True
    return False


def parse_results(html: str) -> list[tuple[str, str]]:
    """Return (url, title) pairs from a DuckDuckGo HTML result page."""
    soup = BeautifulSoup(html, "lxml")
    results: list[tuple[str, str]] = []
    for a in soup.select("a.result__a"):
        href = a.get("href") or ""
        if href.startswith("//"):
            href = "https:" + href
        parsed = urlparse(href)
        if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
            target = parse_qs(parsed.query).get("uddg", [""])[0]
            if target:
                href = target
        title = a.get_text(" ", strip=True)
        if href:
            results.append((href, title))
    return results


def search_backend(api_key: str | None = None) -> str:
    return "brave" if (api_key or os.environ.get("GTM_BRAVE_API_KEY")) else "duckduckgo"


async def search_web(fetcher: HttpFetcher, settings: EngineSettings, query: str,
                     *, brave_api_key: str | None = None) -> list[tuple[str, str]]:
    """Run one web search and return (url, title) pairs. Brave when a key is present, else the
    keyless DuckDuckGo HTML endpoint. Shared by WebsiteFinder (name -> site) and by web-search
    discovery (query -> companies)."""
    key = brave_api_key or os.environ.get("GTM_BRAVE_API_KEY")
    if key:
        result = await fetcher.get(BRAVE_URL.format(q=quote_plus(query)), delay=1.1, api=True,
                                   headers={"X-Subscription-Token": key, "Accept": "application/json"})
        if result.ok:
            try:
                items = json.loads(result.text).get("web", {}).get("results", [])
                return [(i.get("url", ""), i.get("title", "")) for i in items]
            except json.JSONDecodeError:
                pass
        log.debug("brave search failed (%s %s); falling back to duckduckgo", result.status_code, result.error)
    result = await fetcher.get(SEARCH_URL.format(q=quote_plus(query)), delay=settings.search_delay_s, api=True)
    if not result.ok:
        log.debug("search: failed for %r (%s)", query, result.error or result.status_code)
        return []
    return parse_results(result.text)


class WebsiteFinder:
    def __init__(self, fetcher: HttpFetcher, settings: EngineSettings,
                 *, brave_api_key: str | None = None):
        self.fetcher = fetcher
        self.settings = settings
        self.brave_api_key = brave_api_key

    @property
    def backend(self) -> str:
        return search_backend(self.brave_api_key)

    async def _results(self, query: str) -> list[tuple[str, str]]:
        return await search_web(self.fetcher, self.settings, query,
                                brave_api_key=self.brave_api_key)

    async def find(self, company_name: str, city: str | None, country: str | None) -> str | None:
        if not self.settings.enable_search_fallback:
            return None
        query = " ".join(p for p in (company_name, city, country, "official website") if p)
        for href, title in (await self._results(query))[:8]:
            domain = canonical_domain(href)
            if not domain or is_social_url(href) or domain in _DIRECTORY_DOMAINS:
                continue
            if name_matches(company_name, domain, title):
                return f"https://{domain}"
        return None
