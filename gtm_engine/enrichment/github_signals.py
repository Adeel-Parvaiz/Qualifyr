"""GitHub org activity: recent pushes/releases across a company's public repos are a
proxy for an active, staffed engineering function - relevant both as a buying signal
(they have a technical team and a budget) and for personalization. Public REST API,
keyless at 60 req/hr per IP, or 5000 req/hr with GTM_GITHUB_READ_TOKEN (see
docs/API_KEYS.md - deliberately separate from GTM_GITHUB_TOKEN, which is an
actions:write PAT for triggering our own workflows and has no business being handed to
a job that only reads third-party orgs). Opportunistic like the other external signals:
a miss (no org, rate-limited, private repos only) is silent.

A guessed slug ("acme" from acme.pk or "Acme Retail") is not proof of ownership - GitHub
org names are first-come-first-served, so a slug match can land on someone else's org
entirely. Scored only after the org's own profile confirms it: its public `blog` URL
must resolve to the same registrable domain as the company's website."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

from gtm_engine.scraping.fetcher import Fetcher
from gtm_engine.validation.domains import registrable_domain

ORG_URL = "https://api.github.com/orgs/{org}"
REPOS_URL = "https://api.github.com/orgs/{org}/repos?type=public&sort=pushed&direction=desc&per_page=5"

_SLUG_RE = re.compile(r"[^a-z0-9-]+")


def _headers() -> dict[str, str]:
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    token = os.environ.get("GTM_GITHUB_READ_TOKEN") or os.environ.get("GTM_GITHUB_TOKEN")
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _org_slug(company_name: str, domain: str | None) -> str | None:
    slug = _SLUG_RE.sub("", (domain.split(".")[0] if domain else company_name.lower()))
    return slug or None


@dataclass
class GithubActivity:
    org: str
    url: str
    public_repos: int
    last_pushed_at: str | None
    stars_total: int


async def _org_matches_domain(fetcher: Fetcher, slug: str, domain: str | None, headers: dict[str, str]) -> bool:
    if not domain:
        return False
    result = await fetcher.get(ORG_URL.format(org=slug), api=True, headers=headers)
    if not result.ok:
        return False
    try:
        profile = json.loads(result.text)
    except json.JSONDecodeError:
        return False
    if not isinstance(profile, dict):
        return False
    return registrable_domain(profile.get("blog")) == registrable_domain(domain)


async def github_activity(fetcher: Fetcher, company_name: str, domain: str | None) -> GithubActivity | None:
    slug = _org_slug(company_name, domain)
    if not slug:
        return None
    headers = _headers()
    if not await _org_matches_domain(fetcher, slug, domain, headers):
        return None
    result = await fetcher.get(REPOS_URL.format(org=slug), api=True, headers=headers)
    if not result.ok:
        return None
    try:
        repos = json.loads(result.text)
    except json.JSONDecodeError:
        return None
    if not isinstance(repos, list) or not repos:
        return None
    pushed = [r.get("pushed_at") for r in repos if r.get("pushed_at")]
    if not pushed:
        return None
    stars = sum(r.get("stargazers_count", 0) for r in repos)
    return GithubActivity(org=slug, url=f"https://github.com/{slug}", public_repos=len(repos),
                           last_pushed_at=max(pushed), stars_total=stars)
