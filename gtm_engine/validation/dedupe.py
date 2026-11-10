"""Deduplication of discovered companies: domain first, then name+city."""

from __future__ import annotations

import re
from typing import Iterable

from gtm_engine.models import DiscoveredCompany
from gtm_engine.validation.domains import canonical_domain, company_key, is_social_url

_LEGAL_SUFFIX_RE = re.compile(
    r"\b(pvt\.?|private|ltd\.?|limited|llc|inc\.?|co\.?|company|corp\.?|smc-pvt|\(pvt\)|\(private\))\b",
    re.IGNORECASE,
)


def normalize_name(name: str) -> str:
    n = _LEGAL_SUFFIX_RE.sub(" ", name.lower())
    n = re.sub(r"[^a-z0-9]+", " ", n)
    return " ".join(n.split())


def dedupe_companies(companies: Iterable[DiscoveredCompany]) -> list[DiscoveredCompany]:
    """Merge duplicates, preferring the record that carries a website. Order is preserved
    by first appearance so discovery priority (tier-1 cities first) survives."""
    by_key: dict[str, DiscoveredCompany] = {}
    order: list[str] = []
    for c in companies:
        if not c.name or not c.name.strip():
            continue
        if c.website and is_social_url(c.website):
            c = c.model_copy(update={"website": None, "domain": None})
        domain = c.domain or canonical_domain(c.website)
        c = c.model_copy(update={"domain": domain})
        key = company_key(domain, normalize_name(c.name), c.city)
        if key not in by_key:
            by_key[key] = c
            order.append(key)
            continue
        existing = by_key[key]
        by_key[key] = _merge(existing, c)
    # A domain-less record whose name+city matches a domain record is the same company.
    name_index = {
        (normalize_name(c.name), (c.city or "").lower()): k
        for k, c in by_key.items() if c.domain
    }
    for key in list(order):
        c = by_key.get(key)
        if c is None or c.domain:
            continue
        twin = name_index.get((normalize_name(c.name), (c.city or "").lower()))
        if twin and twin != key:
            by_key[twin] = _merge(by_key[twin], c)
            del by_key[key]
            order.remove(key)
    return [by_key[k] for k in order]


def _merge(a: DiscoveredCompany, b: DiscoveredCompany) -> DiscoveredCompany:
    data = a.model_dump()
    for field, value in b.model_dump().items():
        if field == "extra":
            data["extra"] = {**b.extra, **a.extra}
        elif field == "source" and value and value not in data["source"]:
            data["source"] = f"{data['source']}+{value}"
        elif not data.get(field) and value:
            data[field] = value
    return DiscoveredCompany.model_validate(data)
