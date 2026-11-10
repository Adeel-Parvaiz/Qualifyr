"""Internal diagnostic: run 50 campaigns through the real pipeline to see how runs behave
across a mix of cities/industries/discovery sources, without touching the production database
or frontend.

Two modes, run back to back to compare the same 50 campaigns with vs without the paid-tier-free
enrichment keys:
  - baseline (default): Brave/Groq/Gemini only, Google Places and Hunter explicitly disabled.
    Output: log.txt.
  - --with-all-keys: adds Google Places (enrichment, capped at the existing
    places_max_companies_per_run budget - never raised, see docs/API_KEYS.md "shared key" note)
    and Hunter (email verification, free tier 50 credits/month shared pool).
    Output: log_2.txt.

Isolation: writes land in a disposable Postgres schema (`internal_test_<date>` or
`internal_test_allkeys_<date>`), the same `options=-csearch_path=` mechanism
tests/conftest.py uses for test isolation. The production schema (`public`, what the deployed
app reads) is never opened for writes by this script.

Usage: python -m scripts.internal_test_batch [--max-companies N] [--limit N] [--with-all-keys]
Output: log.txt or log_2.txt at the repo root (one line per campaign + a final totals block +
a priority breakdown by score band), not committed.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import time
import urllib.parse
from datetime import date
from pathlib import Path

import httpx
import psycopg

from gtm_engine.config import load_defaults, load_settings
from gtm_engine.config.schema import CampaignConfig, GeographyConfig
from gtm_engine.pipeline import Pipeline
from gtm_engine.scraping.browser import build_fetcher
from gtm_engine.scraping.fetcher import HttpFetcher
from gtm_engine.storage.database import Database

PROJECT_ROOT = Path(__file__).resolve().parents[1]

CITIES = ["Islamabad", "Lahore", "Karachi", "Faisalabad", "Rawalpindi"]

NICHES = [
    dict(slug="clothing", offer="point-of-sale software for clothing boutiques",
         target_industries=["retail"], buyer_keywords=["boutique", "garments", "fashion"],
         osm_categories=["shop=clothes"], overture_categories=["clothing"],
    dict(slug="pharmacy", offer="inventory management software for pharmacies",
         target_industries=["healthcare", "retail"], buyer_keywords=["pharmacy", "chemist", "medical store"],
         osm_categories=["amenity=pharmacy"], overture_categories=["pharmacy"],
    dict(slug="mobile-shop", offer="repair-shop ticketing software for mobile phone shops",
         target_industries=["retail", "electronics"], buyer_keywords=["mobile shop", "cell phone", "mobile accessories"],
         osm_categories=["shop=mobile_phone"], overture_categories=["mobile phone"],
    dict(slug="restaurant", offer="online ordering system for restaurants",
         target_industries=["food", "hospitality"], buyer_keywords=["restaurant", "cafe", "diner"],
         osm_categories=["amenity=restaurant"], overture_categories=["restaurant"],
    dict(slug="gym", offer="membership management software for gyms",
         target_industries=["fitness"], buyer_keywords=["gym", "fitness club", "fitness center"],
         osm_categories=["leisure=fitness_centre"], overture_categories=["gym"],
    dict(slug="auto-repair", offer="appointment scheduling software for auto repair shops",
         target_industries=["automotive"], buyer_keywords=["auto workshop", "car repair", "garage"],
         osm_categories=["shop=car_repair"], overture_categories=["auto repair"],
    dict(slug="salon", offer="booking software for beauty salons",
         target_industries=["beauty"], buyer_keywords=["salon", "parlour", "beauty"],
         osm_categories=["shop=hairdresser"], overture_categories=["hair salon"],
    dict(slug="furniture", offer="custom-order tracking software for furniture stores",
         target_industries=["retail", "home"], buyer_keywords=["furniture", "home decor"],
         osm_categories=["shop=furniture"], overture_categories=["furniture"],
    dict(slug="bakery", offer="order management software for bakeries",
         target_industries=["food"], buyer_keywords=["bakery", "cake shop", "patisserie"],
         osm_categories=["shop=bakery"], overture_categories=["bakery"],
    dict(slug="hardware", offer="B2B ordering portal for hardware stores",
         target_industries=["retail", "construction"], buyer_keywords=["hardware store", "building materials"],
         osm_categories=["shop=hardware"], overture_categories=["hardware"],
]


def build_campaigns(max_companies: int) -> list[CampaignConfig]:
    campaigns = []
    for i, niche in enumerate(NICHES):
        for j, city in enumerate(CITIES):
            cid = f"internal-test-{niche['slug']}-{city.lower()}"
            campaigns.append(CampaignConfig(
                campaign_id=cid,
                name=f"[internal test] {niche['slug']} / {city}",
                offer=niche["offer"],
                target_industries=niche["target_industries"],
                buyer_keywords=niche["buyer_keywords"],
                geography=GeographyConfig(countries=["Pakistan"], cities=[city]),
                osm_categories=niche["osm_categories"],
                overture_categories=niche["overture_categories"],
                max_companies=max_companies,
                min_outreach_ready=0,  # single pass; this batch measures the baseline, not the floor feature
            ))
    return campaigns


# --------------------------------------------------------------------------- usage instrumentation

USAGE = {
    "brave": {"calls": 0},
    "groq": {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0},
    "gemini": {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "models": {}},
}

# Live-confirmed 2026 rates (WebSearch, see log.txt header). Groq free tier incurs no real
# charge; this is the paid-tier equivalent so the number means something if/when we pay.
GROQ_RATE_PER_M = {"input": 0.075, "output": 0.30}        # openai/gpt-oss-20b
GEMINI_RATE_PER_M = {"gemini-3-flash-preview": {"input": 0.50, "output": 3.00}}
BRAVE_RATE_PER_1000 = 5.0                                   # docs/API_KEYS.md


def _install_usage_hooks() -> None:
    orig_fetcher_get = HttpFetcher.get

    async def patched_fetcher_get(self, url, **kwargs):
        if "api.search.brave.com" in url:
            USAGE["brave"]["calls"] += 1
        return await orig_fetcher_get(self, url, **kwargs)

    HttpFetcher.get = patched_fetcher_get

    orig_post = httpx.AsyncClient.post

    async def patched_post(self, url, *args, **kwargs):
        resp = await orig_post(self, url, *args, **kwargs)
        try:
            if "api.groq.com" in url:
                data = resp.json()
                usage = data.get("usage") or {}
                USAGE["groq"]["calls"] += 1
                USAGE["groq"]["prompt_tokens"] += usage.get("prompt_tokens", 0)
                USAGE["groq"]["completion_tokens"] += usage.get("completion_tokens", 0)
            elif "generativelanguage.googleapis.com" in url:
                data = resp.json()
                usage = data.get("usageMetadata") or {}
                USAGE["gemini"]["calls"] += 1
                USAGE["gemini"]["prompt_tokens"] += usage.get("promptTokenCount", 0)
                USAGE["gemini"]["completion_tokens"] += usage.get("candidatesTokenCount", 0)
                m = re.search(r"/models/([^:]+):", url)
                if m:
                    USAGE["gemini"]["models"][m.group(1)] = USAGE["gemini"]["models"].get(m.group(1), 0) + 1
        except Exception:
            pass  # usage capture must never break a real run
        return resp

    httpx.AsyncClient.post = patched_post


def _cost_lines() -> list[str]:
    lines = []
    brave_calls = USAGE["brave"]["calls"]
    lines.append(f"brave:  {brave_calls} calls "
                 f"(~${brave_calls / 1000 * BRAVE_RATE_PER_1000:.4f} at ${BRAVE_RATE_PER_1000}/1000 searches, "
                 f"first 50/day free - see docs/API_KEYS.md)")
    g = USAGE["groq"]
    groq_cost = (g["prompt_tokens"] / 1e6 * GROQ_RATE_PER_M["input"]
                 + g["completion_tokens"] / 1e6 * GROQ_RATE_PER_M["output"])
    lines.append(f"groq:   {g['calls']} calls, {g['prompt_tokens']} prompt + {g['completion_tokens']} completion tokens "
                 f"(~${groq_cost:.4f} at paid-tier $0.075/$0.30 per M in/out for openai/gpt-oss-20b; "
                 f"ran on the FREE tier, so actual billed cost is $0)")
    gm = USAGE["gemini"]
    known_rate = GEMINI_RATE_PER_M.get("gemini-3-flash-preview")
    if known_rate and gm["calls"]:
        gemini_cost = (gm["prompt_tokens"] / 1e6 * known_rate["input"]
                       + gm["completion_tokens"] / 1e6 * known_rate["output"])
        cost_note = f"~${gemini_cost:.4f} at paid-tier $0.50/$3.00 per M in/out for gemini-3-flash-preview"
    else:
        cost_note = "rate not pinned per model this run; token counts are authoritative"
    lines.append(f"gemini: {gm['calls']} calls, {gm['prompt_tokens']} prompt + {gm['completion_tokens']} completion tokens "
                 f"({cost_note}; models hit: {gm['models']}; ran on the FREE tier, so actual billed cost is $0)")
    return lines


# --------------------------------------------------------------------------- main

async def main(max_companies: int, limit: int | None, with_all_keys: bool) -> None:
    _install_usage_hooks()

    base_settings = load_settings()
    if not base_settings.database_url:
        raise SystemExit("GTM_DATABASE_URL not set")

    schema = f"internal_test_{'allkeys_' if with_all_keys else ''}{date.today():%Y%m%d}"
    sep = "&" if "?" in base_settings.database_url else "?"
    isolated_dsn = f"{base_settings.database_url}{sep}options={urllib.parse.quote(f'-csearch_path={schema}')}"

    # The schema must exist before a search_path-scoped connection can create tables in it.
    with psycopg.connect(base_settings.database_url, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')

    settings = base_settings.model_copy(update={
        "database_url": isolated_dsn,
        # places_max_companies_per_run stays at its config default (20) even in --with-all-keys
        # mode - the Google Places key is shared with another project, never raise this budget.
        "enable_places_enrichment": with_all_keys,
        "google_places_api_key": os.environ.get("GTM_GOOGLE_PLACES_API_KEY") if with_all_keys else None,
        "email_verification": "hunter" if with_all_keys else "direct",
        "enable_llm": True,
    })

    resolved_keys = {
        "brave": os.environ.get("GTM_BRAVE_API_KEY"),
        "groq": os.environ.get("GTM_GROQ_API_KEY"),
        "gemini": os.environ.get("GTM_GEMINI_API_KEY"),
    }
    if with_all_keys:
        resolved_keys["places"] = os.environ.get("GTM_GOOGLE_PLACES_API_KEY")

    defaults = load_defaults()
    db = Database(isolated_dsn, ensure_schema=True)

    campaigns = build_campaigns(max_companies)
    if limit:
        campaigns = campaigns[:limit]

    keys_desc = ("brave, groq, gemini, google places "
                 f"(places capped at {settings.places_max_companies_per_run}/run - shared key budget, not raised)"
                 if with_all_keys else
                 "brave, groq, gemini (places disabled; email verifier: direct SMTP)")
    log_lines = [
        f"internal test batch - {len(campaigns)} campaigns, schema={schema}, max_companies={max_companies}",
        f"keys in use: {keys_desc}",
        "",
    ]
    priority_totals = {"high_priority": 0, "qualified": 0, "review": 0, "reject": 0}

    for i, campaign in enumerate(campaigns, 1):
        provider = "groq" if i % 2 else "gemini"
        campaign_settings = settings.model_copy(update={"llm_provider": provider})
        before = {k: dict(v) if not isinstance(v, dict) else v for k, v in
                  {"brave": USAGE["brave"].copy(), "groq": USAGE["groq"].copy(), "gemini": USAGE["gemini"].copy()}.items()}
        t0 = time.monotonic()
        try:
            async with build_fetcher(campaign_settings) as fetcher:
                pipeline = Pipeline(campaign_settings, defaults, db, fetcher, resolved_keys=resolved_keys)
                result = await pipeline.run(campaign)
            s = result.stats
            elapsed = time.monotonic() - t0
            d_brave = USAGE["brave"]["calls"] - before["brave"]["calls"]
            d_groq_calls = USAGE["groq"]["calls"] - before["groq"]["calls"]
            d_groq_tok = (USAGE["groq"]["prompt_tokens"] + USAGE["groq"]["completion_tokens"]) - \
                         (before["groq"]["prompt_tokens"] + before["groq"]["completion_tokens"])
            d_gemini_calls = USAGE["gemini"]["calls"] - before["gemini"]["calls"]
            d_gemini_tok = (USAGE["gemini"]["prompt_tokens"] + USAGE["gemini"]["completion_tokens"]) - \
                           (before["gemini"]["prompt_tokens"] + before["gemini"]["completion_tokens"])
            by_priority = db.campaign_stats(campaign.campaign_id, campaign.min_score)["by_priority"]
            for k in priority_totals:
                priority_totals[k] += by_priority.get(k, 0)
            line = (f"[{i:02d}/{len(campaigns)}] {campaign.campaign_id:<35} "
                    f"processed={s.processed:<4} qualified={s.qualified:<3} outreach_ready={s.outreach_ready:<3} "
                    f"target_met={s.target_met} | brave={d_brave} groq_calls={d_groq_calls} groq_tok={d_groq_tok} "
                    f"gemini_calls={d_gemini_calls} gemini_tok={d_gemini_tok} | {elapsed:.1f}s | "
                    f"priority: high={by_priority.get('high_priority', 0)} qualified={by_priority.get('qualified', 0)} "
                    f"review={by_priority.get('review', 0)} reject={by_priority.get('reject', 0)}")
        except Exception as exc:  # one bad campaign must not kill the batch
            elapsed = time.monotonic() - t0
            line = f"[{i:02d}/{len(campaigns)}] {campaign.campaign_id:<35} FAILED after {elapsed:.1f}s: {exc!r}"
        print(line, flush=True)
        log_lines.append(line)

    log_lines.append("")
    log_lines.append("=== totals ===")
    log_lines.extend(_cost_lines())
    log_lines.append("")
    log_lines.append(f"priority breakdown (all campaigns): high_priority={priority_totals['high_priority']} "
                     f"qualified={priority_totals['qualified']} review={priority_totals['review']} "
                     f"reject={priority_totals['reject']}")

    # Sanity check: the production schema's campaign list must be unchanged by this batch.
    prod_db = Database(base_settings.database_url, ensure_schema=False)
    prod_count = len(prod_db.list_campaigns())
    prod_db.close()
    log_lines.append("")
    log_lines.append(f"production-schema campaign count after batch: {prod_count} "
                     f"(isolation check - this run never wrote here)")

    db.close()

    out_path = PROJECT_ROOT / ("log_2.txt" if with_all_keys else "log.txt")
    out_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--max-companies", type=int, default=30)
    p.add_argument("--limit", type=int, default=None, help="run only the first N campaigns (testing this script itself)")
    p.add_argument("--with-all-keys", action="store_true",
                   help="also enable Google Places + Hunter; writes log_2.txt instead of log.txt")
    args = p.parse_args()
    asyncio.run(main(args.max_companies, args.limit, args.with_all_keys))
