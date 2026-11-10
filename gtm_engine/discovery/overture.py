"""Overture Maps Places: a bulk, openly licensed business dataset (Meta/Microsoft-derived)
published as parquet on public S3. Queried in place with DuckDB, no key, no download.
Roughly 40x OSM's coverage for Pakistani cities, with websites and emails."""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, Iterable

from gtm_engine.config.schema import CampaignConfig, EngineSettings
from gtm_engine.discovery.geocode import BBox, Geocoder
from gtm_engine.models import DiscoveredCompany
from gtm_engine.scraping.fetcher import HttpFetcher

log = logging.getLogger(__name__)

S3_ROOT = "s3://overturemaps-us-west-2/release"
COLUMNS = ["id", "name", "category", "website", "email", "phone", "address", "city", "region",
           "brand", "lat", "lon"]


# The struct holding a place's category. Overture renamed it from `categories` to
# `taxonomy` in release 2026-09-23.0, and _query() always reads whichever release is
# newest - so an upstream rename breaks every run with no commit on our side, and the
# error ("Referenced table not found") names a table, which sends you looking at SQL
# syntax rather than at the schema. Newest name first; probe rather than assume.
CATEGORY_COLUMNS = ("taxonomy", "categories")


def pick_category_column(available: Iterable[str]) -> str:
    available = set(available)
    for name in CATEGORY_COLUMNS:
        if name in available:
            return name
    raise RuntimeError(
        f"Overture places has none of {CATEGORY_COLUMNS} (columns: {sorted(available)}). "
        "The schema changed again; add the new name to CATEGORY_COLUMNS."
    )


def build_sql(release: str, bbox: BBox, categories: list[str], limit: int | None,
              category_column: str = CATEGORY_COLUMNS[0]) -> tuple[str, list[str]]:
    # Bracket access, not dot: `primary` is a reserved word, and the struct is nested
    # deep enough that a bare dot chain reads as schema.table.column when it fails.
    cat_expr = f"{category_column}['primary']"
    cat_clause = ""
    params: list[str] = []
    if categories:
        cat_clause = "AND (" + " OR ".join(f"lower({cat_expr}) LIKE ?" for _ in categories) + ")"
        params = [f"%{c.lower()}%" for c in categories]
    sql = f"""
        SELECT id, names['primary'] AS name, {cat_expr} AS category,
               CASE WHEN length(websites)>0 THEN websites[1] END AS website,
               CASE WHEN length(emails)>0   THEN emails[1]   END AS email,
               CASE WHEN length(phones)>0   THEN phones[1]   END AS phone,
               addresses[1].freeform AS address, addresses[1].locality AS city,
               addresses[1].region AS region,
               brand.names['primary'] AS brand, bbox.ymin AS lat, bbox.xmin AS lon
        FROM read_parquet('{S3_ROOT}/{release}/theme=places/type=place/*', hive_partitioning=1)
        WHERE bbox.xmin BETWEEN {bbox.west} AND {bbox.east}
          AND bbox.ymin BETWEEN {bbox.south} AND {bbox.north}
          AND names['primary'] IS NOT NULL
          {cat_clause}
        {f'LIMIT {int(limit)}' if limit else ''}
    """
    return sql, params


def region_matches(region: str | None, tokens: tuple[str, ...] | list[str]) -> bool:
    """Whether a place's region field is consistent with the searched area's region tokens.

    Tolerant in both directions because the two vocabularies rarely match exactly: Overture may
    say "Madrid" where Nominatim says "comunidad de madrid", or "VA" where Nominatim gives both
    "virginia" and the ISO suffix "va". An empty region or no tokens is never treated as a
    contradiction - only a region that matches nothing is."""
    value = (region or "").strip().lower()
    if not value or not tokens:
        return True
    return any(value == t or value in t or t in value for t in tokens)


def filter_by_region(rows: list[dict], tokens: tuple[str, ...] | list[str],
                     area: str) -> list[dict]:
    """Drop places whose own region contradicts the searched area's.

    A bbox is axis-aligned and so cannot exclude a neighbour that a concave region wraps around:
    the 2026-10-13 "Virginia" run returned Charleston and Huntington, West Virginia, and all three
    qualified leads were WV agencies labelled as Virginia. The region field is the only signal in
    the data that can separate them.

    Fail-safe, same reasoning as the area proximity filter: if almost nothing survives, the two
    datasets' region vocabularies did not line up rather than the area being genuinely empty, so
    the results are kept unfiltered and the mismatch logged."""
    if not rows or not tokens:
        return rows
    kept = [r for r in rows if region_matches(r.get("region"), tokens)]
    if len(kept) < max(1, len(rows) // 10):
        log.warning("overture: region filter would drop %d/%d places for %s (tokens=%s; regions "
                    "seen=%s) - treating as a region-vocabulary mismatch and keeping them all",
                    len(rows) - len(kept), len(rows), area, ",".join(tokens),
                    ",".join(sorted({(r.get("region") or "?") for r in rows})[:8]))
        return rows
    if len(kept) < len(rows):
        log.info("overture: region filter kept %d, dropped %d outside %s (%s)",
                 len(kept), len(rows) - len(kept), area, ",".join(tokens))
    return kept


def row_to_company(row: dict, city: str, country: str) -> DiscoveredCompany | None:
    name = (row.get("name") or "").strip()
    if not name:
        return None
    return DiscoveredCompany(
        name=name,
        website=row.get("website") or None,
        country=country,
        # The city we searched, same as OSM: Overture's locality is unreliable as a display value.
        # The 2026-10-13 Lahore run returned Urdu tehsil names for it ("تحصیل ماڈل ٹاؤن"), a
        # neighbourhood rather than a city, and malformed values like "Bahria TownLahore". The real
        # locality is kept in extra["addr_city"] for anything that wants it. This used to be able
        # to assert a flat untruth - a Charleston WV business exported as "Virginia" - but
        # filter_by_region() now guarantees the place really is in the searched area's region.
        city=city,
        address=row.get("address") or None,
        phone=row.get("phone") or None,
        email=(row.get("email") or "").lower() or None,
        category=f"overture={row['category']}" if row.get("category") else None,
        source="overture",
        source_url=f"https://explore.overturemaps.org/#{row['id']}" if row.get("id") else None,
        extra={"brand": row.get("brand"), "lat": row.get("lat"), "lon": row.get("lon"),
               "addr_city": row.get("city")},
    )


class OvertureDiscovery:
    name = "overture"

    def __init__(self, fetcher: HttpFetcher, settings: EngineSettings, geocoder: Geocoder | None = None,
                 per_city_limit: int | None = None):
        self.settings = settings
        self.geocoder = geocoder or Geocoder(fetcher, settings.db_path.parent / "geocode_cache.json")
        self.per_city_limit = per_city_limit

    def _query(self, bbox: BBox, categories: list[str]) -> list[dict]:
        try:
            import duckdb
        except ImportError:
            log.warning("overture: duckdb not installed (pip install -e '.[overture]'); skipping")
            return []
        con = duckdb.connect()
        con.execute("INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';")
        release = con.execute(
            "SELECT max(regexp_extract(file, 'release/([^/]+)/', 1)) "
            f"FROM glob('{S3_ROOT}/*/theme=places/type=place/*')"
        ).fetchone()[0]
        dataset = f"{S3_ROOT}/{release}/theme=places/type=place/*"
        columns = [r[0] for r in con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{dataset}', hive_partitioning=1)"
        ).fetchall()]
        sql, params = build_sql(release, bbox, categories, self.per_city_limit,
                                pick_category_column(columns))
        rows = con.execute(sql, params).fetchall()
        con.close()
        return [dict(zip(COLUMNS, r)) for r in rows]

    async def discover(self, campaign: CampaignConfig) -> AsyncIterator[DiscoveredCompany]:
        if not campaign.overture_categories:
            log.info("overture: campaign has no overture_categories; skipping")
            return
        countries = campaign.geography.countries or [""]
        for area in campaign.geography.search_areas(include_country_fallback=True):
            bbox, country = None, countries[0]
            for c in countries:
                bbox = await self.geocoder.bbox(area, c or None)
                if bbox is not None:
                    country = c
                    break
            if bbox is None:
                log.warning("overture: could not geocode %s; skipping", area)
                continue
            rows = await asyncio.to_thread(self._query, bbox, campaign.overture_categories)
            log.info("overture: %s -> %d places", area, len(rows))
            rows = filter_by_region(rows, bbox.region_tokens, area)
            for row in rows:
                company = row_to_company(row, area, country or "")
                if company:
                    yield company
