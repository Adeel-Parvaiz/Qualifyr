"""Job-board signals: open roles on Greenhouse or Lever are a public, structured proxy
for hiring - stronger evidence than a "we're hiring" phrase on a homepage, since it lists
actual role titles, locations and dates. A growth-function role (procurement, sales,
business development, a new city) is weighted higher than routine backfill.

Both APIs are public and keyless. Only a guessed slug (from the domain or company name)
is needed; a miss just means the company uses neither ATS, which is common and silent."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from gtm_engine.config.schema import DefaultRules
from gtm_engine.scraping.fetcher import Fetcher

GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=false"
LEVER_URL = "https://api.lever.co/v0/postings/{slug}?mode=json"

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug_candidates(company_name: str, domain: str | None) -> list[str]:
    candidates: list[str] = []
    if domain:
        candidates.append(domain.split(".")[0])
    name_slug = _SLUG_RE.sub("", company_name.lower())
    if name_slug:
        candidates.append(name_slug)
    out: list[str] = []
    for c in candidates:
        if c and c not in out:
            out.append(c)
    return out[:2]


@dataclass
class JobPosting:
    title: str
    location: str | None
    url: str | None
    board: str
    growth_role: bool = False


@dataclass
class JobBoardResult:
    board: str | None = None
    slug: str | None = None
    postings: list[JobPosting] = field(default_factory=list)


def _mark_growth(postings: list[JobPosting], growth_roles: list[str]) -> None:
    for p in postings:
        low = p.title.lower()
        p.growth_role = any(r in low for r in growth_roles)


async def job_board_signals(fetcher: Fetcher, company_name: str, domain: str | None,
                             defaults: DefaultRules) -> JobBoardResult:
    for slug in _slug_candidates(company_name, domain):
        gh = await fetcher.get(GREENHOUSE_URL.format(slug=slug), api=True)
        if gh.ok:
            try:
                jobs = json.loads(gh.text).get("jobs", [])
            except (json.JSONDecodeError, AttributeError):
                jobs = []
            if jobs:
                postings = [
                    JobPosting(title=j.get("title", ""), location=(j.get("location") or {}).get("name"),
                               url=j.get("absolute_url"), board="greenhouse")
                    for j in jobs[:20] if j.get("title")
                ]
                _mark_growth(postings, defaults.job_growth_roles)
                return JobBoardResult(board="greenhouse", slug=slug, postings=postings)

        lv = await fetcher.get(LEVER_URL.format(slug=slug), api=True)
        if lv.ok:
            try:
                jobs = json.loads(lv.text)
            except json.JSONDecodeError:
                jobs = []
            if isinstance(jobs, list) and jobs:
                postings = [
                    JobPosting(title=j.get("text", ""), location=(j.get("categories") or {}).get("location"),
                               url=j.get("hostedUrl"), board="lever")
                    for j in jobs[:20] if j.get("text")
                ]
                _mark_growth(postings, defaults.job_growth_roles)
                return JobBoardResult(board="lever", slug=slug, postings=postings)

    return JobBoardResult()
