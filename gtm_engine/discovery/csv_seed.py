"""User-supplied seed list. Accepts loose column names so an exported sheet works as-is."""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import AsyncIterator

from gtm_engine.config.schema import CampaignConfig
from gtm_engine.models import DiscoveredCompany

log = logging.getLogger(__name__)

_ALIASES = {
    "name": ("name", "company", "company_name", "business", "business_name", "organization"),
    "website": ("website", "url", "site", "domain", "web"),
    "city": ("city", "town", "location"),
    "country": ("country",),
    "address": ("address", "street", "addr"),
    "phone": ("phone", "telephone", "mobile", "contact_number"),
    "email": ("email", "e-mail", "contact_email"),
    "category": ("category", "industry", "type", "sector"),
}


def _pick(row: dict[str, str], field: str) -> str | None:
    lowered = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
    for alias in _ALIASES[field]:
        if lowered.get(alias):
            return lowered[alias]
    return None


class CSVSeedDiscovery:
    name = "csv_seed"

    def __init__(self, path: Path):
        self.path = path

    async def discover(self, campaign: CampaignConfig) -> AsyncIterator[DiscoveredCompany]:
        if not self.path.exists():
            log.warning("csv_seed: %s not found", self.path)
            return
        default_country = campaign.geography.countries[0] if campaign.geography.countries else None
        with self.path.open("r", encoding="utf-8-sig", newline="") as fh:
            for i, row in enumerate(csv.DictReader(fh), start=2):
                name = _pick(row, "name")
                if not name:
                    continue
                yield DiscoveredCompany(
                    name=name,
                    website=_pick(row, "website"),
                    city=_pick(row, "city"),
                    country=_pick(row, "country") or default_country,
                    address=_pick(row, "address"),
                    phone=_pick(row, "phone"),
                    email=_pick(row, "email"),
                    category=_pick(row, "category"),
                    source="csv_seed",
                    source_url=f"{self.path.name}#row={i}",
                )
