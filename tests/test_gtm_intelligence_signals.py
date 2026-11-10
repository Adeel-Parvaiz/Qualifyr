"""GTM intelligence: job-board postings (Greenhouse/Lever), GitHub org activity, press/RSS
mentions. All keyless public APIs, all opportunistic - a miss is silent, never an error."""

import httpx
import respx

from gtm_engine.enrichment.github_signals import github_activity
from gtm_engine.enrichment.job_signals import job_board_signals
from gtm_engine.enrichment.press_signals import press_mentions
from gtm_engine.scraping.fetcher import HttpFetcher

RSS_FEED = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<item><title>Acme launches new inventory platform</title><link>https://acme.pk/news/1</link><pubDate>Mon, 01 Sep 2026 00:00:00 GMT</pubDate></item>
<item><title>Acme raises Series A funding</title><link>https://acme.pk/news/2</link><pubDate>Mon, 15 Aug 2026 00:00:00 GMT</pubDate></item>
<item><title>Unrelated update about office snacks</title><link>https://acme.pk/news/3</link><pubDate>Mon, 01 Jul 2026 00:00:00 GMT</pubDate></item>
</channel></rss>"""

ATOM_FEED = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Acme opens new office in Lahore</title><link href="https://acme.pk/blog/expand"/><updated>2026-09-01T00:00:00Z</updated></entry>
</feed>"""


# --- job boards --------------------------------------------------------------------------

@respx.mock
async def test_greenhouse_job_board_found(settings, defaults):
    defaults.job_growth_roles = ["procurement", "sales manager"]
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=false").mock(
        return_value=httpx.Response(200, json={"jobs": [
            {"title": "Procurement Officer", "location": {"name": "Karachi"}, "absolute_url": "https://x/1"},
            {"title": "Frontend Engineer", "location": {"name": "Remote"}, "absolute_url": "https://x/2"},
        ]}))
    async with HttpFetcher(settings) as fetcher:
        result = await job_board_signals(fetcher, "Acme", "acme.com", defaults)
    assert result.board == "greenhouse" and result.slug == "acme"
    assert len(result.postings) == 2
    assert result.postings[0].growth_role is True
    assert result.postings[1].growth_role is False


@respx.mock
async def test_lever_used_when_greenhouse_has_no_board(settings, defaults):
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=false").mock(
        return_value=httpx.Response(404))
    respx.get("https://api.lever.co/v0/postings/acme?mode=json").mock(
        return_value=httpx.Response(200, json=[
            {"text": "Business Development Manager", "categories": {"location": "Lahore"}, "hostedUrl": "https://x/3"},
        ]))
    async with HttpFetcher(settings) as fetcher:
        result = await job_board_signals(fetcher, "Acme", "acme.com", defaults)
    assert result.board == "lever" and len(result.postings) == 1
    assert result.postings[0].title == "Business Development Manager"


@respx.mock
async def test_job_board_miss_is_silent(settings, defaults):
    respx.get(url__startswith="https://boards-api.greenhouse.io/").mock(return_value=httpx.Response(404))
    respx.get(url__startswith="https://api.lever.co/").mock(return_value=httpx.Response(404))
    async with HttpFetcher(settings) as fetcher:
        result = await job_board_signals(fetcher, "Acme", "acme.com", defaults)
    assert result.board is None and result.postings == []


# --- github --------------------------------------------------------------------------------

@respx.mock
async def test_github_activity_found(settings):
    # The org's own profile must confirm ownership (its `blog` matches the company's
    # domain) before its repos are even fetched.
    respx.get("https://api.github.com/orgs/acme").mock(
        return_value=httpx.Response(200, json={"login": "acme", "blog": "https://acme.com"}))
    respx.get(url__startswith="https://api.github.com/orgs/acme/repos").mock(
        return_value=httpx.Response(200, json=[
            {"pushed_at": "2026-09-01T00:00:00Z", "stargazers_count": 10},
            {"pushed_at": "2026-08-01T00:00:00Z", "stargazers_count": 5},
        ]))
    async with HttpFetcher(settings) as fetcher:
        gh = await github_activity(fetcher, "Acme", "acme.com")
    assert gh.org == "acme" and gh.public_repos == 2 and gh.stars_total == 15
    assert gh.last_pushed_at == "2026-09-01T00:00:00Z"


@respx.mock
async def test_github_activity_no_org_is_silent(settings):
    respx.get(url__startswith="https://api.github.com/orgs/").mock(return_value=httpx.Response(404))
    async with HttpFetcher(settings) as fetcher:
        gh = await github_activity(fetcher, "Acme", "acme.com")
    assert gh is None


@respx.mock
async def test_github_activity_rejects_an_unrelated_org_with_the_same_slug(settings):
    """Org names are first-come-first-served: "acme" existing is not proof it is *our*
    Acme. A profile whose `blog` points somewhere else must not be scored."""
    respx.get("https://api.github.com/orgs/acme").mock(
        return_value=httpx.Response(200, json={"login": "acme", "blog": "https://unrelated-acme.io"}))
    respx.get(url__startswith="https://api.github.com/orgs/acme/repos").mock(
        return_value=httpx.Response(200, json=[{"pushed_at": "2026-09-01T00:00:00Z", "stargazers_count": 10}]))
    async with HttpFetcher(settings) as fetcher:
        gh = await github_activity(fetcher, "Acme", "acme.com")
    assert gh is None


@respx.mock
async def test_github_activity_no_blog_on_profile_is_unverified(settings):
    respx.get("https://api.github.com/orgs/acme").mock(
        return_value=httpx.Response(200, json={"login": "acme", "blog": ""}))
    async with HttpFetcher(settings) as fetcher:
        gh = await github_activity(fetcher, "Acme", "acme.com")
    assert gh is None


# --- press / rss -----------------------------------------------------------------------------

@respx.mock
async def test_press_mentions_rss_classified(settings, defaults):
    respx.get("https://acme.pk/feed").mock(return_value=httpx.Response(200, text=RSS_FEED, headers={"content-type": "application/rss+xml"}))
    async with HttpFetcher(settings) as fetcher:
        mentions = await press_mentions(fetcher, "https://acme.pk", defaults)
    kinds = {m.kind for m in mentions}
    assert kinds == {"product_launch", "funding"}
    assert all("office snacks" not in m.title.lower() for m in mentions)


@respx.mock
async def test_press_mentions_falls_back_across_feed_paths(settings, defaults):
    respx.get("https://acme.pk/feed").mock(return_value=httpx.Response(404))
    respx.get("https://acme.pk/rss.xml").mock(return_value=httpx.Response(404))
    respx.get("https://acme.pk/blog/feed").mock(return_value=httpx.Response(200, text=ATOM_FEED, headers={"content-type": "application/atom+xml"}))
    async with HttpFetcher(settings) as fetcher:
        mentions = await press_mentions(fetcher, "https://acme.pk", defaults)
    assert len(mentions) == 1 and mentions[0].kind == "expansion_press"


@respx.mock
async def test_press_mentions_no_feed_is_silent(settings, defaults):
    respx.get(url__startswith="https://acme.pk/").mock(return_value=httpx.Response(404))
    async with HttpFetcher(settings) as fetcher:
        mentions = await press_mentions(fetcher, "https://acme.pk", defaults)
    assert mentions == []
