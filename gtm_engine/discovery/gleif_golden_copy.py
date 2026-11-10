"""GLEIF Golden Copy: the full LEI-CDF bulk dataset (millions of records, ~1 GB+), queried
in place with DuckDB from a local Parquet file. Unlike the live `gleif.py` API source (one
HTTP request per campaign query), this reads the entire LEI universe at once - useful when a
campaign's `country_codes` filter is broader than a handful of name queries would cover.

Not fetched automatically: `scripts/fetch_bulk_datasets.py --dataset gleif` downloads and
converts the raw Golden Copy CSV to this Parquet file as a one-time manual step
(see docs/API_KEYS.md). Missing file -> explicit RuntimeError pointing at that script, not a
silent skip - this source is opt-in (`enable_gleif_golden_copy`), so silence here would hide a
setup mistake rather than a normal "nothing configured" state."""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from gtm_engine.config.schema import CampaignConfig, EngineSettings
from gtm_engine.models import DiscoveredCompany

log = logging.getLogger(__name__)

COLUMNS = ["lei", "legal_name", "legal_country", "legal_city", "legal_address_lines",
           "jurisdiction", "registration_status", "entity_status"]


def build_sql(path: str, country_codes: list[str], limit: int | None) -> tuple[str, list[str]]:
    country_clause = ""
    params: list[str] = []
    if country_codes:
        country_clause = "AND legal_country IN (" + ",".join("?" for _ in country_codes) + ")"
        params = list(country_codes)
    sql = f"""
        SELECT "LEI" AS lei,
               "Entity.LegalName" AS legal_name,
               "Entity.LegalAddress.Country" AS legal_country,
               "Entity.LegalAddress.City" AS legal_city,
               "Entity.LegalAddress.FirstAddressLine" AS legal_address_lines,
               "Entity.LegalJurisdiction" AS jurisdiction,
               "Registration.RegistrationStatus" AS registration_status,
               "Entity.EntityStatus" AS entity_status
        FROM read_parquet('{path}')
        WHERE "Entity.LegalName" IS NOT NULL
          {country_clause}
        {f'LIMIT {int(limit)}' if limit else ''}
    """
    return sql, params


def row_to_company(row: dict) -> DiscoveredCompany | None:
    name = (row.get("legal_name") or "").strip()
    if not name:
        return None
    return DiscoveredCompany(
        name=name,
        country=row.get("legal_country"),
        city=row.get("legal_city"),
        address=row.get("legal_address_lines") or None,
        category="registry=gleif_golden_copy",
        source="gleif_golden_copy",
        source_url=f"https://search.gleif.org/#/record/{row['lei']}" if row.get("lei") else None,
        extra={
            "lei": row.get("lei"),
            "jurisdiction": row.get("jurisdiction"),
            "registration_status": row.get("registration_status"),
            "entity_status": row.get("entity_status"),
        },
    )


class GLEIFGoldenCopyDiscovery:
    name = "gleif_golden_copy"

    def __init__(self, settings: EngineSettings, per_run_limit: int | None = None):
        self.settings = settings
        self.per_run_limit = per_run_limit

    def _query(self, country_codes: list[str]) -> list[dict]:
        try:
            import duckdb
        except ImportError:
            log.warning("gleif_golden_copy: duckdb not installed (pip install -e '.[overture]'); skipping")
            return []
        path = self.settings.gleif_golden_copy_path
        if not path.exists():
            raise RuntimeError(
                f"GLEIF Golden Copy file not found at {path}. Run "
                "`python scripts/fetch_bulk_datasets.py --dataset gleif` first (see docs/API_KEYS.md)."
            )
        con = duckdb.connect()
        sql, params = build_sql(str(path), country_codes, self.per_run_limit)
        rows = con.execute(sql, params).fetchall()
        columns = [d[0] for d in con.description]
        con.close()
        return [dict(zip(columns, r)) for r in rows]

    async def discover(self, campaign: CampaignConfig) -> AsyncIterator[DiscoveredCompany]:
        if not self.settings.enable_gleif_golden_copy:
            return
        rows = await asyncio.to_thread(self._query, campaign.geography.country_codes)
        log.info("gleif_golden_copy: %d rows matched", len(rows))
        for row in rows:
            company = row_to_company(row)
            if company:
                yield company
