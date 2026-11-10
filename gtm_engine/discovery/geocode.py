"""City -> bounding box via Nominatim (OpenStreetMap's geocoder). Free; usage policy asks
for an identifying User-Agent and at most one request per second. Results are cached on
disk because city boxes never change between runs."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

from gtm_engine.scraping.fetcher import HttpFetcher

log = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
# Nominatim's usage policy caps the public instance at 1 request/second; 1.1s stays under it.
# Results are also cached to disk (see __init__), so a re-run and repeated cities cost nothing.
# Override for a self-hosted instance with a higher limit via GTM_NOMINATIM_DELAY_S.
NOMINATIM_DELAY_S = float(os.environ.get("GTM_NOMINATIM_DELAY_S", "1.1"))


@dataclass(frozen=True)
class BBox:
    south: float
    west: float
    north: float
    east: float
    # Names/codes of the administrative region the area sits in ("virginia", "va"), from
    # Nominatim's addressdetails. A bounding box is axis-aligned, so for a concave region it
    # necessarily covers its neighbours: Virginia's box spans -83.7..-75.2 and 36.5..39.5, which
    # contains the whole of West Virginia. Discovery sources that carry a region field can use
    # these tokens to drop the neighbours the box could not exclude.
    region_tokens: tuple[str, ...] = ()

    def overpass(self) -> str:
        return f"{self.south},{self.west},{self.north},{self.east}"


# Nominatim's addressdetails keys that name the region a place belongs to, broadest-useful first.
_REGION_KEYS = ("state", "province", "region", "state_district", "county")


def _region_tokens(address: dict) -> tuple[str, ...]:
    """Lowercased region names plus the suffix of any ISO-3166-2 code, e.g.
    {"virginia", "va"} for Virginia or {"islamabad capital territory", "is"} for Islamabad.
    Both forms are needed because region vocabularies differ per dataset: Overture stores "VA"
    for US places but spelled-out names in much of the world."""
    out: list[str] = []
    for key in _REGION_KEYS:
        value = (address.get(key) or "").strip().lower()
        if value and value not in out:
            out.append(value)
    for key, value in address.items():
        if key.startswith("ISO3166-2") and isinstance(value, str) and "-" in value:
            code = value.rsplit("-", 1)[1].strip().lower()
            if code and code not in out:
                out.append(code)
    return tuple(out)


class Geocoder:
    def __init__(self, fetcher: HttpFetcher, cache_path: Path | None = None):
        self.fetcher = fetcher
        self.cache_path = cache_path
        self._cache: dict[str, dict] = {}
        if cache_path and cache_path.exists():
            try:
                self._cache = json.loads(cache_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                self._cache = {}

    async def bbox(self, city: str, country: str | None) -> BBox | None:
        key = f"{city}|{country or ''}".lower()
        cached = self._cache.get(key)
        # Entries written before region_tokens existed are re-fetched rather than trusted: a box
        # with no region tokens silently disables the region filter for that area.
        if cached is not None and "region_tokens" in cached:
            return BBox(**{**cached, "region_tokens": tuple(cached["region_tokens"])})
        params = {"q": f"{city}, {country}" if country else city, "format": "json", "limit": 1,
                  "addressdetails": 1}
        result = await self.fetcher.get(f"{NOMINATIM_URL}?{urlencode(params)}", delay=NOMINATIM_DELAY_S, api=True)
        if not result.ok:
            log.warning("geocode: %s failed (%s %s)", city, result.status_code, result.error)
            return None
        try:
            hits = json.loads(result.text)
        except json.JSONDecodeError:
            return None
        if not hits or "boundingbox" not in hits[0]:
            log.warning("geocode: no result for %r", params["q"])
            return None
        south, north, west, east = (float(v) for v in hits[0]["boundingbox"])
        box = BBox(south=south, west=west, north=north, east=east,
                   region_tokens=_region_tokens(hits[0].get("address") or {}))
        self._cache[key] = {**box.__dict__, "region_tokens": list(box.region_tokens)}
        if self.cache_path:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(self._cache, indent=1), encoding="utf-8")
        log.info("geocode: %s -> %s", params["q"], box.overpass())
        return box
