import json

import httpx
import pytest
import respx

from conftest import fixture
from gtm_engine.discovery.geocode import NOMINATIM_URL, BBox, Geocoder
from gtm_engine.discovery.osm import OSMDiscovery, build_query, element_to_company, tag_filter
from gtm_engine.discovery.search import WebsiteFinder, name_matches, parse_results
from gtm_engine.scraping.fetcher import HttpFetcher


def test_tag_filter():
    assert tag_filter("shop=clothes") == '["shop"="clothes"]'
    assert tag_filter("shop=*") == '["shop"]'
    assert tag_filter("amenity") == '["amenity"]'


def test_build_query_uses_bbox_and_categories():
    q = build_query(BBox(33.5, 72.8, 33.8, 73.2), ["shop=clothes", "shop=*"], 60)
    assert 'nwr["shop"="clothes"]["name"](33.5,72.8,33.8,73.2);' in q
    assert 'nwr["shop"]["name"](33.5,72.8,33.8,73.2);' in q
    assert "[timeout:60]" in q
    assert "out center tags" in q


def test_element_to_company_maps_tags():
    el = json.loads(fixture("overpass_islamabad.json"))["elements"][0]
    c = element_to_company(el, "Islamabad", "Pakistan", None)
    assert c.name == "Zara Fabrics"
    assert c.website == "https://www.zarafabrics.pk/"
    assert c.category == "shop=clothes"
    assert c.phone == "+92 51 2345678"
    assert c.address == "Blue Area, Islamabad"
    assert c.source_url == "https://www.openstreetmap.org/node/1"


def test_element_without_name_is_dropped():
    assert element_to_company({"type": "node", "id": 5, "tags": {"shop": "clothes"}}, "X", "PK", None) is None


def _mock_nominatim():
    return respx.get(url__startswith=NOMINATIM_URL).mock(
        return_value=httpx.Response(200, json=[{"boundingbox": ["33.5", "33.8", "72.8", "73.2"]}]))


@respx.mock
async def test_geocoder_captures_region_tokens(settings, tmp_path):
    """The bbox alone cannot exclude a neighbouring region, so the region's names and ISO code
    are carried alongside it for discovery sources that can check them."""
    respx.get(url__startswith=NOMINATIM_URL).mock(return_value=httpx.Response(200, json=[{
        "boundingbox": ["36.5", "39.4", "-83.6", "-75.1"],
        "address": {"state": "Virginia", "country": "United States",
                    "ISO3166-2-lvl4": "US-VA"}}]))
    async with HttpFetcher(settings) as fetcher:
        box = await Geocoder(fetcher, tmp_path / "geo.json").bbox("Virginia", "United States")
    assert box.region_tokens == ("virginia", "va")


@respx.mock
async def test_geocoder_refetches_cache_entries_written_before_region_tokens(settings, tmp_path):
    """A pre-existing cache entry has no region tokens; trusting it would silently disable the
    region filter for exactly the areas that have already been run."""
    cache = tmp_path / "geo.json"
    cache.write_text(json.dumps({"virginia|united states": {
        "south": 36.5, "west": -83.6, "north": 39.4, "east": -75.1}}), encoding="utf-8")
    route = respx.get(url__startswith=NOMINATIM_URL).mock(return_value=httpx.Response(200, json=[{
        "boundingbox": ["36.5", "39.4", "-83.6", "-75.1"],
        "address": {"ISO3166-2-lvl4": "US-VA"}}]))
    async with HttpFetcher(settings) as fetcher:
        box = await Geocoder(fetcher, cache).bbox("Virginia", "United States")
    assert route.call_count == 1 and box.region_tokens == ("va",)


@respx.mock
async def test_geocoder_caches_to_disk(settings, tmp_path):
    route = _mock_nominatim()
    cache = tmp_path / "geo.json"
    async with HttpFetcher(settings) as fetcher:
        g = Geocoder(fetcher, cache)
        box = await g.bbox("Islamabad", "Pakistan")
        assert box == BBox(33.5, 72.8, 33.8, 73.2)
        assert await Geocoder(HttpFetcher(settings), cache).bbox("Islamabad", "Pakistan") == box
    assert route.call_count == 1


@respx.mock
async def test_osm_discovery_yields_companies(campaign, settings):
    _mock_nominatim()
    respx.get(url__startswith=settings.overpass_url).mock(
        return_value=httpx.Response(200, json=json.loads(fixture("overpass_islamabad.json")))
    )
    async with HttpFetcher(settings) as fetcher:
        found = [c async for c in OSMDiscovery(fetcher, settings).discover(campaign)]
    # 5 named elements x 2 cities (same mocked response), unnamed one dropped
    assert len(found) == 10
    assert all(c.source == "osm" for c in found)
    assert {c.city for c in found} == {"Islamabad", "Rawalpindi"}


@respx.mock
async def test_osm_failure_is_empty_not_exception(campaign, settings):
    _mock_nominatim()
    respx.get(url__startswith=settings.overpass_url).mock(return_value=httpx.Response(504))
    async with HttpFetcher(settings) as fetcher:
        found = [c async for c in OSMDiscovery(fetcher, settings).discover(campaign)]
    assert found == []


# --- search fallback -----------------------------------------------------------

DDG_HTML = """
<html><body>
<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.facebook.com%2Fkhaadi">Khaadi - Facebook</a>
<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.daraz.pk%2Fkhaadi">Khaadi on Daraz</a>
<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.khaadi.com%2Fpk%2F">Khaadi | Official Online Store</a>
</body></html>
"""


def test_parse_results_unwraps_redirects():
    res = parse_results(DDG_HTML)
    assert res[0][0] == "https://www.facebook.com/khaadi"
    assert res[2] == ("https://www.khaadi.com/pk/", "Khaadi | Official Online Store")


def test_name_matches_is_conservative():
    assert name_matches("Khaadi", "khaadi.com", None)
    assert name_matches("Al Fatah Electronics", "alfatah.pk", None)
    assert not name_matches("Al Fatah Electronics", "electronics.pk", "Electronics store")
    assert name_matches("Sapphire Retail", "sapphireonline.pk", "Sapphire | Online Store")
    assert not name_matches("Sapphire Retail", "random.pk", "Buy stuff online")


@respx.mock
async def test_website_finder_skips_directories(campaign, settings):
    settings.enable_search_fallback = True
    respx.get(url__startswith="https://html.duckduckgo.com/html/").mock(return_value=httpx.Response(200, text=DDG_HTML))
    async with HttpFetcher(settings) as fetcher:
        found = await WebsiteFinder(fetcher, settings).find("Khaadi", "Lahore", "Pakistan")
    assert found == "https://khaadi.com"


async def test_website_finder_disabled(settings):
    async with HttpFetcher(settings) as fetcher:
        assert await WebsiteFinder(fetcher, settings).find("Khaadi", None, None) is None


def test_osm_city_is_the_searched_city_not_urdu_tag():
    el = {"type": "node", "id": 9, "tags": {"name": "Bata", "shop": "shoes", "addr:city": "راولپنڈی"}}
    c = element_to_company(el, "Rawalpindi", "Pakistan", None)
    assert c.city == "Rawalpindi" and c.extra["addr_city"] == "راولپنڈی"


def test_name_matches_label_inside_name():
    assert name_matches("ElectricStorePk Electric Store", "electricstore.pk", None)
    assert not name_matches("XS Mobile", "whatmobile.com.pk", "WhatMobile - phone prices")
    assert not name_matches("Food 24 Hours", "archivesouthasia.com", None)


@respx.mock
async def test_overpass_mirror_fallback(campaign, settings):
    _mock_nominatim()
    settings.overpass_mirrors = ["https://mirror.test/api/interpreter"]
    respx.get(url__startswith=settings.overpass_url).mock(return_value=httpx.Response(504))
    mirror = respx.get(url__startswith="https://mirror.test/").mock(
        return_value=httpx.Response(200, json=json.loads(fixture("overpass_islamabad.json"))))
    campaign.geography.cities = ["Islamabad"]
    async with HttpFetcher(settings) as fetcher:
        found = [c async for c in OSMDiscovery(fetcher, settings).discover(campaign)]
    assert mirror.called and len(found) == 5


def test_overture_row_mapping_and_sql():
    from gtm_engine.discovery.overture import build_sql, row_to_company
    sql, params = build_sql("2026-08-19.0", BBox(33.5, 72.8, 33.8, 73.2), ["clothing", "shoe_store"], 50)
    assert "release/2026-08-19.0/theme=places" in sql and "LIMIT 50" in sql
    assert params == ["%clothing%", "%shoe_store%"] and sql.count("LIKE ?") == 2
    c = row_to_company({"id": "abc", "name": "Cell story", "category": "mobile_phone_store",
                        "website": "http://www.cellstory.pk/", "email": "Info@CellStory.pk", "brand": None}, "Islamabad", "Pakistan")
    assert c.source == "overture" and c.category == "overture=mobile_phone_store"
    assert c.email == "info@cellstory.pk" and c.city == "Islamabad"
    assert row_to_company({"name": ""}, "Islamabad", "Pakistan") is None


def test_overture_city_is_the_searched_area_not_the_places_locality():
    """Overture's locality looked like the better value for the city column - the 2026-10-13
    Virginia run exported Charleston WV businesses as "Virginia" - but switching to it put Urdu
    tehsil names in the Lahore run's city column and one "Bahria TownLahore". It is kept in
    extra["addr_city"] instead; filter_by_region is what stops the area label being a lie."""
    from gtm_engine.discovery.overture import row_to_company

    c = row_to_company({"id": "x", "name": "Redwell Pharmacy",
                        "city": "تحصیل ماڈل ٹاؤن"}, "Lahore", "Pakistan")
    assert c.city == "Lahore"
    assert c.extra["addr_city"] == "تحصیل ماڈل ٹاؤن"


def test_overture_region_filter_drops_neighbours_a_bbox_cannot_exclude():
    """Regression (2026-10-13 Virginia run): Virginia's bounding box is axis-aligned, so it
    contains the whole of West Virginia. All three qualified leads - Digital Relativity,
    Bulldog Creative Services, Ahmdigital - were WV agencies. Only the region field separates
    them, and it has to match tolerantly: Nominatim gives "virginia"/"va", Overture says "VA"."""
    from gtm_engine.discovery.overture import filter_by_region, region_matches

    tokens = ("virginia", "va")
    assert region_matches("VA", tokens)
    assert not region_matches("WV", tokens)
    # Spelled-out vs code, and a wider official name vs the short one.
    assert region_matches("Madrid", ("comunidad de madrid", "md"))
    # No region on the place, or no tokens for the area: never a contradiction.
    assert region_matches(None, tokens) and region_matches("WV", ())

    rows = [{"name": f"va{i}", "region": "VA"} for i in range(9)] + [
        {"name": "wv1", "region": "WV"}, {"name": "oh1", "region": "OH"}]
    kept = filter_by_region(rows, tokens, "Virginia")
    assert [r["name"] for r in kept] == [f"va{i}" for i in range(9)]


def test_overture_region_filter_keeps_everything_when_vocabularies_disagree():
    """If the two datasets name regions differently the filter must not zero the run - same
    fail-safe as the area proximity filter."""
    from gtm_engine.discovery.overture import filter_by_region

    rows = [{"name": str(i), "region": "Islamabad Capital Territory"} for i in range(20)]
    assert filter_by_region(rows, ("federal capital", "zz"), "Islamabad") == rows


def test_overture_category_column_follows_the_release_schema():
    """Overture renamed `categories` to `taxonomy` in 2026-09-23.0 and the query reads
    whichever release is newest, so this broke in production with no commit on our side.
    The old test asserted on the release path and the LIKE count and stayed green."""
    from gtm_engine.discovery.overture import build_sql, pick_category_column

    assert pick_category_column(["id", "names", "taxonomy"]) == "taxonomy"
    assert pick_category_column(["id", "names", "categories"]) == "categories"
    # Both present (a transition release): prefer the current name.
    assert pick_category_column(["categories", "taxonomy"]) == "taxonomy"

    with pytest.raises(RuntimeError, match="schema changed again"):
        pick_category_column(["id", "names", "websites"])

    for column in ("taxonomy", "categories"):
        sql, _ = build_sql("2026-08-19.0", BBox(33.5, 72.8, 33.8, 73.2), ["clothing"], 5, column)
        assert f"{column}['primary']" in sql
        other = "categories" if column == "taxonomy" else "taxonomy"
        assert other not in sql
        # `primary` is reserved; a bare dot chain is what produced the original
        # "Referenced table not found" error rather than an unknown-column one.
        assert ".primary" not in sql
