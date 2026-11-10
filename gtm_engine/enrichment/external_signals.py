"""Signals from outside the company's own website, all keyless:

  site quality  - copyright year, mobile viewport, page weight (from HTML we already have)
  domain age    - RDAP (works for .com/.net/.org/...; PKNIC offers no RDAP for .pk)
  news mentions - GDELT DOC 2.0 API (1 request / 5 s enforced by GDELT; we honour it)

Every signal is recorded with its source so it can be shown to the reviewer."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import quote

from gtm_engine.scraping.fetcher import Fetcher

log = logging.getLogger(__name__)

_YEAR_RE = re.compile(r"(?:©|&copy;|copyright)\s*(?:\(c\)\s*)?(?:\d{4}\s*[-–]\s*)?(20\d{2}|19\d{2})", re.I)
_VIEWPORT_RE = re.compile(r'<meta[^>]+name=["\']viewport["\']', re.I)

RDAP_URL = "https://rdap.org/domain/{domain}"
GDELT_URL = ("https://api.gdeltproject.org/api/v2/doc/doc?query={q}&mode=artlist&format=json"
             "&maxrecords=8&timespan=6months&sort=datedesc")
GDELT_MIN_INTERVAL_S = 5.5


@dataclass
class SiteQuality:
    copyright_year: int | None = None
    mobile_friendly: bool | None = None
    page_weight_kb: int | None = None
    notes: list[str] = field(default_factory=list)


def site_quality(html_by_kind: dict[str, str], now_year: int | None = None) -> SiteQuality:
    home = html_by_kind.get("home", "")
    if not home:
        return SiteQuality()
    q = SiteQuality()
    years = [int(y) for y in _YEAR_RE.findall(" ".join(html_by_kind.values()))]
    if years:
        q.copyright_year = max(years)
        this_year = now_year or datetime.now(timezone.utc).year
        if q.copyright_year <= this_year - 3:
            q.notes.append(f"site copyright {q.copyright_year}: possibly unmaintained")
    q.mobile_friendly = bool(_VIEWPORT_RE.search(home))
    if not q.mobile_friendly:
        q.notes.append("no mobile viewport")
    q.page_weight_kb = len(home.encode("utf-8", "ignore")) // 1024
    if q.page_weight_kb > 3000:
        q.notes.append(f"heavy homepage ({q.page_weight_kb} KB)")
    return q


@dataclass
class DomainAge:
    registered: datetime | None
    years: float | None
    source: str
    note: str = ""


async def domain_age(fetcher: Fetcher, domain: str | None, now: datetime | None = None) -> DomainAge | None:
    if not domain:
        return None
    if domain.endswith(".pk"):
        return DomainAge(None, None, "rdap", "no RDAP for .pk (PKNIC); age unknown")
    result = await fetcher.get(RDAP_URL.format(domain=domain), api=True)
    if not result.ok:
        return DomainAge(None, None, "rdap", f"rdap {result.status_code or result.error}")
    try:
        data = json.loads(result.text)
    except json.JSONDecodeError:
        return DomainAge(None, None, "rdap", "rdap: bad json")
    registered = None
    for ev in data.get("events", []):
        if ev.get("eventAction") == "registration" and ev.get("eventDate"):
            try:
                registered = datetime.fromisoformat(ev["eventDate"].replace("Z", "+00:00"))
            except ValueError:
                pass
    if registered is None:
        return DomainAge(None, None, "rdap", "rdap: no registration event")
    now = now or datetime.now(timezone.utc)
    years = (now - registered).days / 365.25
    return DomainAge(registered, round(years, 1), "rdap")


@dataclass
class NewsMention:
    title: str
    url: str
    date: str
    source: str


class NewsChecker:
    """GDELT article search for the company name. Serialised and throttled process-wide."""

    def __init__(self, fetcher: Fetcher, min_interval_s: float = GDELT_MIN_INTERVAL_S):
        self.fetcher = fetcher
        self.min_interval_s = min_interval_s
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def mentions(self, company_name: str, country: str = "Pakistan", *, wait: bool = False) -> list[NewsMention]:
        """GDELT allows one request per 5 s, so this is opportunistic by default: if another
        company is already using the slot, the signal is skipped rather than stalling the
        batch behind it. News is a bonus signal, never a reason to slow the pipeline."""
        name = re.sub(r"[^\w\s&.-]", " ", company_name).strip()
        if len(name) < 4:
            return []
        query = quote(f'"{name}" {country}')
        if not wait and self._lock.locked():
            log.debug("gdelt: slot busy, skipping news for %s", name)
            return []
        async with self._lock:
            pause = self._last + self.min_interval_s - time.monotonic()
            if pause > 0:
                await asyncio.sleep(pause)
            result = await self.fetcher.get(GDELT_URL.format(q=query), api=True)
            self._last = time.monotonic()
        if not result.ok:
            log.debug("gdelt: %s %s", result.status_code, result.error)
            return []
        try:
            arts = json.loads(result.text).get("articles", [])
        except json.JSONDecodeError:
            return []
        out = []
        low = name.lower()
        for a in arts:
            title = a.get("title") or ""
            # GDELT's phrase match is loose; keep only articles that actually name the company.
            if low not in title.lower() and low not in (a.get("url") or "").lower():
                continue
            out.append(NewsMention(title=title[:140], url=a.get("url", ""), date=(a.get("seendate") or "")[:8],
                                   source=a.get("domain", "")))
        return out[:5]
