"""Phase D: chamber directory (KCCI), site quality, domain age (RDAP), news (GDELT), Brave finder."""

import json
from datetime import datetime, timezone

import httpx
import respx

from gtm_engine.discovery.chambers import KCCI_URL, KCCIDirectory, clean_rep, name_matches_campaign, parse_kcci, title_case
from gtm_engine.discovery.search import WebsiteFinder
from gtm_engine.enrichment.external_signals import GDELT_MIN_INTERVAL_S, NewsChecker, domain_age, site_quality
from gtm_engine.scraping.fetcher import HttpFetcher

KCCI_HTML = """<html><body><table>
<tr><th>MSNo</th><th>CompanyName</th><th>RepName</th></tr>
<tr><td>2</td><td>PIONEER CEMENT LTD.</td><td>Syed Mohsin Raza Naqvi</td></tr>
<tr><td>10</td><td>A.C.T. INDUSTRIES (PVT) LTD.</td><td>Zafar Iqbal Allawala</td></tr>
<tr><td>77</td><td>AL-KARAM TEXTILE MILLS (PVT) LTD</td><td>Mr. FAWAD ANWAR</td></tr>
<tr><td>78</td><td>KHAADI GARMENTS</td><td></td></tr>
<tr><td>x</td><td>not a row</td><td></td></tr>
</table></body></html>"""


def test_parse_kcci_rows():
    rows = parse_kcci(KCCI_HTML)
    assert [r["company"] for r in rows] == ["PIONEER CEMENT LTD.", "A.C.T. INDUSTRIES (PVT) LTD.",
                                             "AL-KARAM TEXTILE MILLS (PVT) LTD", "KHAADI GARMENTS"]
    assert rows[0]["representative"] == "Syed Mohsin Raza Naqvi" and rows[3]["representative"] is None


def test_title_case_and_rep_cleaning():
    assert title_case("AL-KARAM TEXTILE MILLS") == "Al-karam Textile Mills"
    assert title_case("A.C.T. INDUSTRIES") == "A.C.T. Industries"
    assert clean_rep("Mr. FAWAD ANWAR") == "Fawad Anwar"
    assert clean_rep("Munir Bhimjee.") == "Munir Bhimjee"
    assert clean_rep(None) is None


def test_name_filter_uses_campaign_terms(campaign):
    campaign.chamber_name_keywords = ["textile"]
    assert name_matches_campaign("AL-KARAM TEXTILE MILLS (PVT) LTD", campaign)
    assert name_matches_campaign("KHAADI GARMENTS", campaign) is False  # 'garments' not in retail test campaign
    assert name_matches_campaign("PIONEER CEMENT LTD.", campaign) is False


@respx.mock
async def test_kcci_source_yields_companies_with_representatives(campaign, settings, tmp_path):
    respx.get(KCCI_URL).mock(return_value=httpx.Response(200, text=KCCI_HTML, headers={"content-type": "text/html"}))
    campaign.geography.cities = ["Karachi"]
    campaign.chamber_name_keywords = ["textile", "cement"]
    async with HttpFetcher(settings) as fetcher:
        src = KCCIDirectory(fetcher, settings)
        found = [c async for c in src.discover(campaign)]
    assert [c.name for c in found] == ["Pioneer Cement", "Al-karam Textile Mills"]
    assert found[0].extra["representative"] == "Syed Mohsin Raza Naqvi" and found[0].city == "Karachi"
    assert found[1].extra["representative"] == "Fawad Anwar" and found[1].category == "chamber=kcci"
    assert src.cache.exists()  # second run reads the cache, no request
    respx.get(KCCI_URL).mock(return_value=httpx.Response(500))
    async with HttpFetcher(settings) as fetcher:
        again = [c async for c in KCCIDirectory(fetcher, settings).discover(campaign)]
    assert len(again) == 2


async def test_kcci_skipped_unless_karachi(campaign, settings):
    campaign.geography.cities = ["Islamabad"]
    async with HttpFetcher(settings) as fetcher:
        assert [c async for c in KCCIDirectory(fetcher, settings).discover(campaign)] == []


# --- site quality ------------------------------------------------------------------------

def test_site_quality_signals():
    html = {"home": '<html><head><meta name="viewport" content="width=device-width"></head><body>© 2019 Zara Fabrics</body></html>'}
    q = site_quality(html, now_year=2026)
    assert q.copyright_year == 2019 and q.mobile_friendly is True
    assert any("2019" in n for n in q.notes)
    fresh = site_quality({"home": "<html><body>Copyright 2024-2026 Co</body></html>"}, now_year=2026)
    assert fresh.copyright_year == 2026 and fresh.mobile_friendly is False and "no mobile viewport" in fresh.notes
    assert site_quality({}).copyright_year is None


# --- domain age --------------------------------------------------------------------------

@respx.mock
async def test_domain_age_rdap_and_pk_unavailable(settings):
    respx.get("https://rdap.org/domain/metroshoes.com").mock(return_value=httpx.Response(200, json={
        "events": [{"eventAction": "registration", "eventDate": "2016-03-01T00:00:00Z"}]}))
    async with HttpFetcher(settings) as fetcher:
        age = await domain_age(fetcher, "metroshoes.com", now=datetime(2026, 3, 1, tzinfo=timezone.utc))
        pk = await domain_age(fetcher, "bata.com.pk")
    assert age.years == 10.0 and age.source == "rdap"
    assert pk.years is None and "no RDAP for .pk" in pk.note


# --- news -----------------------------------------------------------------------------------

@respx.mock
async def test_news_checker_filters_and_throttles(settings):
    calls = []

    def handler(request):
        calls.append(request.url)
        return httpx.Response(200, json={"articles": [
            {"title": "Khaadi opens flagship store in Karachi", "url": "https://x.pk/a", "seendate": "20260910T120000Z", "domain": "dawn.com"},
            {"title": "Unrelated retail news", "url": "https://x.pk/b", "seendate": "20260901T000000Z", "domain": "tribune.com.pk"},
        ]})
    respx.get(url__startswith="https://api.gdeltproject.org/").mock(side_effect=handler)
    slept = []

    async def fake_sleep(s):
        slept.append(s)
    import gtm_engine.enrichment.external_signals as es
    original = es.asyncio.sleep
    es.asyncio.sleep = fake_sleep
    try:
        async with HttpFetcher(settings) as fetcher:
            nc = NewsChecker(fetcher)
            first = await nc.mentions("Khaadi")
            second = await nc.mentions("Khaadi")
    finally:
        es.asyncio.sleep = original
    assert len(first) == 1 and first[0].source == "dawn.com" and first[0].date == "20260910"
    assert len(calls) == 2 and slept and slept[0] > 0  # second call waited for the GDELT interval


# --- Brave finder ----------------------------------------------------------------------------

@respx.mock
async def test_website_finder_uses_brave_when_key_present(settings, monkeypatch):
    settings.enable_search_fallback = True
    monkeypatch.setenv("GTM_BRAVE_API_KEY", "k")
    route = respx.get(url__startswith="https://api.search.brave.com/").mock(return_value=httpx.Response(200, json={
        "web": {"results": [{"url": "https://www.daraz.pk/khaadi", "title": "Khaadi on Daraz"},
                             {"url": "https://www.khaadi.com/pk/", "title": "Khaadi | Official"}]}}))
    async with HttpFetcher(settings) as fetcher:
        finder = WebsiteFinder(fetcher, settings)
        assert finder.backend == "brave"
        assert await finder.find("Khaadi", "Lahore", "Pakistan") == "https://khaadi.com"
    assert route.called and route.calls[0].request.headers["X-Subscription-Token"] == "k"


@respx.mock
async def test_news_check_is_opportunistic_not_blocking(settings):
    """A busy GDELT slot must not stall the batch: the signal is skipped instead."""
    import asyncio
    respx.get(url__startswith="https://api.gdeltproject.org/").mock(
        return_value=httpx.Response(200, json={"articles": []}))
    async with HttpFetcher(settings) as fetcher:
        nc = NewsChecker(fetcher, min_interval_s=30)
        await nc._lock.acquire()          # another company holds the slot
        try:
            result = await asyncio.wait_for(nc.mentions("Khaadi"), timeout=2)
        finally:
            nc._lock.release()
    assert result == []
