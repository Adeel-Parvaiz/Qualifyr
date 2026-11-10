"""OpenStreetMap discovery via the Overpass API. Free, no key, business-level data
with names, addresses and (sometimes) websites/phones."""

from __future__ import annotations

import json
import logging
from urllib.parse import urlencode
from typing import AsyncIterator

from gtm_engine.config.schema import CampaignConfig, EngineSettings
from gtm_engine.discovery.geocode import BBox, Geocoder
from gtm_engine.models import DiscoveredCompany
from gtm_engine.scraping.fetcher import HttpFetcher

log = logging.getLogger(__name__)

# OSM tag keys that may carry a website / email / phone, in preference order.
_WEBSITE_KEYS = ("website", "contact:website", "url", "contact:url", "brand:website")
_EMAIL_KEYS = ("email", "contact:email")
_PHONE_KEYS = ("phone", "contact:phone", "contact:mobile", "mobile")


def tag_filter(category: str) -> str:
    """'shop=clothes' -> '["shop"="clothes"]'; 'shop=*' or 'shop' -> '["shop"]'."""
    if "=" not in category:
        return f'["{category}"]'
    key, value = category.split("=", 1)
    if value in ("*", ""):
        return f'["{key}"]'
    return f'["{key}"="{value}"]'


def build_query(bbox: BBox, categories: list[str], timeout_s: int) -> str:
    """Bounding-box query. Admin-area names are inconsistent across OSM (there is no
    relation named exactly "Rawalpindi"), so cities are geocoded to a box first."""
    filters = "".join(f'  nwr{tag_filter(c)}["name"]({bbox.overpass()});\n' for c in categories)
    return f"[out:json][timeout:{timeout_s}];\n(\n{filters});\nout center tags;"


def _first(tags: dict, keys: tuple[str, ...]) -> str | None:
    for k in keys:
        v = tags.get(k)
        if v:
            return v.split(";")[0].strip()
    return None


def _address(tags: dict) -> str | None:
    parts = [tags.get("addr:housenumber"), tags.get("addr:street"), tags.get("addr:suburb"),
             tags.get("addr:city"), tags.get("addr:postcode")]
    joined = ", ".join(p for p in parts if p)
    return joined or tags.get("addr:full")


def element_to_company(el: dict, city: str, country: str, category_label: str | None) -> DiscoveredCompany | None:
    tags = el.get("tags") or {}
    name = tags.get("name") or tags.get("brand")
    if not name:
        return None
    osm_type, osm_id = el.get("type"), el.get("id")
    category = None
    for key in ("shop", "amenity", "office", "craft", "industrial", "healthcare"):
        if key in tags:
            category = f"{key}={tags[key]}"
            break
    return DiscoveredCompany(
        name=name.strip(),
        website=_first(tags, _WEBSITE_KEYS),
        country=country,
        city=city,  # the city we searched; OSM addr:city is often in Urdu/Arabic script
        address=_address(tags),
        phone=_first(tags, _PHONE_KEYS),
        email=_first(tags, _EMAIL_KEYS),
        category=category or category_label,
        source="osm",
        source_url=f"https://www.openstreetmap.org/{osm_type}/{osm_id}" if osm_type and osm_id else None,
        extra={"osm_tags": tags, "brand": tags.get("brand"),
               "opening_hours": tags.get("opening_hours"),
               "addr_city": tags.get("addr:city"),
               "lat": el.get("lat") or (el.get("center") or {}).get("lat"),
               "lon": el.get("lon") or (el.get("center") or {}).get("lon")},
    )


class OSMDiscovery:
    name = "osm"

    def __init__(self, fetcher: HttpFetcher, settings: EngineSettings, geocoder: Geocoder | None = None):
        self.fetcher = fetcher
        self.settings = settings
        self.geocoder = geocoder or Geocoder(fetcher, settings.db_path.parent / "geocode_cache.json")
        # Per-run state so one failing area doesn't waste time retrying mirrors we already
        # proved broken this run. `all_mirrors_dead` lets the pipeline mark stats.osm_unavailable.
        self._dead_mirrors: set[str] = set()
        self.all_mirrors_dead: bool = False

    async def discover(self, campaign: CampaignConfig) -> AsyncIterator[DiscoveredCompany]:
        if not campaign.osm_categories:
            log.info("osm: campaign has no osm_categories; skipping")
            return
        countries = campaign.geography.countries or [None]
        total_elements = 0
        areas_queried = 0
        for area in campaign.geography.search_areas(include_country_fallback=True):
            bbox, country = await self._geocode_area(area, countries)
            if bbox is None:
                log.warning("osm: could not geocode %s; skipping", area)
                continue
            query = build_query(bbox, campaign.osm_categories, self.settings.overpass_timeout_s)
            elements = await self._run_query(query)
            areas_queried += 1
            total_elements += len(elements)
            log.info("osm: %s -> %d elements", area, len(elements))
            for el in elements:
                company = element_to_company(el, area, country or "", None)
                if company:
                    yield company
        # All mirrors were marked dead AND we queried at least one area means Overpass was
        # unavailable to us this run. Overture carries PK volume; we don't crash on this.
        all_bases = [self.settings.overpass_url, *self.settings.overpass_mirrors]
        if areas_queried > 0 and total_elements == 0 and all(b in self._dead_mirrors for b in all_bases):
            self.all_mirrors_dead = True
            log.warning("osm: all Overpass mirrors unavailable this run; Overture-only discovery")

    async def _geocode_area(self, area: str, countries: list[str | None]):
        """Resolve an area to a bbox, trying each configured country as a hint. Lets one
        campaign span countries (e.g. 'Lahore' under Pakistan and 'Dubai' under UAE) - the
        first country in which the name resolves wins."""
        for country in countries:
            bbox = await self.geocoder.bbox(area, country)
            if bbox is not None:
                return bbox, country
        return None, countries[0] if countries else None

    async def _run_query(self, query: str) -> list[dict]:
        # Overpass instances are shared and often overloaded; fall through the mirror list.
        # Within one run, a mirror that failed once is skipped for subsequent areas - no point
        # paying the retry+backoff cost per area when the mirror is proven unavailable.
        for base in [self.settings.overpass_url, *self.settings.overpass_mirrors]:
            if base in self._dead_mirrors:
                continue
            url = f"{base}?{urlencode({'data': query})}"
            result = await self.fetcher.get(url, delay=self.settings.per_host_delay_s * 2, api=True)
            if not result.ok:
                log.warning("osm: %s failed (%s %s); marking dead for this run", base, result.status_code, result.error)
                self._dead_mirrors.add(base)
                continue
            try:
                payload = json.loads(result.text)
            except json.JSONDecodeError:
                log.warning("osm: non-JSON response from %s; marking dead for this run", base)
                self._dead_mirrors.add(base)
                continue
            return payload.get("elements", [])
        return []
