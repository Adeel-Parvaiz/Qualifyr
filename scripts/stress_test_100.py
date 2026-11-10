"""Stress test the full pipeline end-to-end against an isolated Supabase schema.

Builds one dense multi-city retail campaign (big surface area so Overture/OSM yield
thousands of candidates) and runs it to min_outreach_ready=100 with a very generous
expansion multiplier. Writes a per-run summary line to the console AND to a log file.

Does NOT touch the production schema - creates a disposable test schema under the same
Supabase project, same pattern tests/conftest.py uses, and drops it on exit (unless
--keep-schema is passed).

Email verification is `direct` (our own SMTP RCPT, default). On a port-25-blocked host
this degrades to MX-only, which still produces mx_valid email_status.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import sys
import time
import urllib.parse
import uuid
from pathlib import Path

import psycopg

from gtm_engine.config.loader import load_defaults, load_settings
from gtm_engine.config.schema import (
    CampaignConfig, GeographyConfig, RoutingThresholds, ScoringWeights,
)
from gtm_engine.export.csv_export import export_path, write_clean_csv, write_csv
from gtm_engine.models import CompanyType, Priority
from gtm_engine.pipeline import Pipeline
from gtm_engine.scraping.fetcher import HttpFetcher
from gtm_engine.storage.database import Database


def _dsn_with_schema(base_dsn: str, schema: str) -> str:
    sep = "&" if "?" in base_dsn else "?"
    return f"{base_dsn}{sep}options={urllib.parse.quote(f'-csearch_path={schema}')}"


@contextlib.contextmanager
def isolated_schema(base_dsn: str, keep: bool = False):
    """Yield (schema_name, scoped_dsn). Create -> yield -> drop (unless keep=True)."""
    schema = f"stress_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(base_dsn, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA "{schema}"')
    try:
        yield schema, _dsn_with_schema(base_dsn, schema)
    finally:
        if not keep:
            with psycopg.connect(base_dsn, autocommit=True) as conn:
                conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def build_campaign(campaign_id: str, max_companies: int, min_outreach_ready: int,
                   max_multiplier: int) -> CampaignConfig:
    """Narrowed to the two densest Pakistani cities (Karachi + Lahore) and the four densest
    retail OSM categories. Six cities × eight categories swamped Overpass with 500s; this
    scopes to a realistic surface area while still exercising every discovery source."""
    return CampaignConfig(
        campaign_id=campaign_id,
        name="stress test: Pakistan retail (clothing + general)",
        offer="Inventory and store-operations automation for multi-outlet retailers",
        target_industries=["retail", "clothing", "fashion", "apparel", "footwear",
                           "general_store", "supermarket"],
        geography=GeographyConfig(
            countries=["Pakistan"],
            cities=["Karachi", "Lahore"],
        ),
        target_roles=["founder", "ceo", "coo", "director", "owner", "general manager",
                      "head of operations", "head of ecommerce", "head of retail", "country manager"],
        buyer_keywords=["retailer", "store", "brand", "outlet", "online store", "shop",
                        "stores across pakistan", "nationwide outlets", "multi-brand",
                        "multi-outlet", "flagship store"],
        osm_categories=[
            "shop=clothes", "shop=shoes", "shop=department_store", "shop=supermarket",
        ],
        overture_categories=["clothing_store", "shoe_store", "department_store", "supermarket"],
        search_queries=[
            "clothing retailer in Pakistan",
            "multi-brand fashion stores Karachi",
            "shoe stores Lahore",
            "supermarket chain Pakistan",
        ],
        chamber_sources=["kcci"],
        intent_sources=["ppra"],
        max_pages_per_site=4,
        max_companies=max_companies,
        min_outreach_ready=min_outreach_ready,
        max_expansion_multiplier=max_multiplier,
        min_score=10,
        routing=RoutingThresholds(qualified=40, high_priority=55, review=20),
        scoring_weights=ScoringWeights(),
    )


async def run_stress(min_target: int, max_companies: int, max_multiplier: int,
                     log_path: Path, keep_schema: bool) -> int:
    base_dsn = os.environ.get("GTM_TEST_DATABASE_URL") or os.environ.get("GTM_DATABASE_URL")
    if not base_dsn:
        print("ERROR: GTM_TEST_DATABASE_URL (or GTM_DATABASE_URL) must be set", file=sys.stderr)
        return 2

    with isolated_schema(base_dsn, keep=keep_schema) as (schema, scoped_dsn):
        print(f"isolated schema: {schema}")
        settings_override = {
            "database_url": scoped_dsn,
            "enable_llm": True,
            "email_verification": "direct",  # own SMTP; degrades to MX-only on blocked networks
            # Shared Places key stays capped at 20/run per CLAUDE.md.
        }
        base_settings = load_settings().model_copy(update=settings_override)
        defaults = load_defaults()

        campaign = build_campaign(
            campaign_id=f"stress-{uuid.uuid4().hex[:8]}",
            max_companies=max_companies,
            min_outreach_ready=min_target,
            max_multiplier=max_multiplier,
        )

        db = Database(scoped_dsn, ensure_schema=True)
        db.upsert_campaign(campaign.campaign_id, campaign.name,
                           campaign.model_dump(mode="json"))

        log_lines = [
            f"stress test to {min_target} outreach-ready; schema={schema}",
            f"max_companies={max_companies} expansion_multiplier={max_multiplier}",
            f"verifier=direct (will degrade to MX-only if port 25 blocked)",
            "",
        ]

        start = time.monotonic()
        try:
            async with HttpFetcher(base_settings) as fetcher:
                pipeline = Pipeline(base_settings, defaults, db, fetcher)
                result = await pipeline.run(campaign, progress=None)
        except Exception as exc:  # noqa: BLE001 - surface it, don't crash the harness
            log_lines.append(f"RUN FAILED: {type(exc).__name__}: {exc}")
            log_path.write_text("\n".join(log_lines), encoding="utf-8")
            db.close()
            raise

        elapsed = time.monotonic() - start
        s = result.stats
        high = sum(1 for l in result.leads if l.priority == Priority.HIGH)
        qualified = sum(1 for l in result.leads if l.priority == Priority.QUALIFIED)
        with_email = sum(1 for l in result.leads
                         if l.priority in (Priority.HIGH, Priority.QUALIFIED)
                         and l.contact_email)
        with_phone = sum(1 for l in result.leads
                         if l.priority in (Priority.HIGH, Priority.QUALIFIED)
                         and l.phone)
        with_both = sum(1 for l in result.leads
                        if l.priority in (Priority.HIGH, Priority.QUALIFIED)
                        and l.contact_email and l.phone)

        log_lines += [
            f"discovered={s.discovered} after_dedupe={s.after_dedupe} processed={s.processed}",
            f"BUYER={s.buyer} VENDOR={s.vendor} UNKNOWN={s.unknown}",
            f"qualified={s.qualified} outreach_ready={s.outreach_ready} target_met={getattr(s, 'target_met', 'n/a')}",
            f"expansion_rounds={getattr(s, 'expansion_rounds', 'n/a')}",
            f"priority: high={high} qualified={qualified} review=... reject=...",
            f"with exec email: {with_email}/{high + qualified}",
            f"with exec phone: {with_phone}/{high + qualified}",
            f"with BOTH email AND phone: {with_both}/{high + qualified}",
            f"elapsed={elapsed:.1f}s",
        ]

        buyers = [l for l in result.leads if l.company_type == CompanyType.BUYER
                  and l.total_score >= campaign.min_score]
        out_dir = Path(base_settings.export_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_q = write_csv(buyers, export_path(out_dir, campaign.campaign_id,
                                              result.run_id, True))
        out_clean = write_clean_csv(buyers, out_dir / f"{campaign.campaign_id}_{result.run_id}_clean.csv")
        log_lines.append(f"qualified CSV: {out_q}")
        log_lines.append(f"client-ready CSV: {out_clean}")

        log_path.write_text("\n".join(log_lines), encoding="utf-8")
        print("\n".join(log_lines))

        db.close()
        return 0 if with_both >= min_target else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=100,
                    help="min outreach-ready executives with email+phone (default 100)")
    ap.add_argument("--max-companies", type=int, default=300,
                    help="per-round discovery cap (default 300)")
    ap.add_argument("--max-multiplier", type=int, default=20,
                    help="expansion multiplier (default 20 -> up to max_companies*20 processed)")
    ap.add_argument("--log", default="stress_test_log.txt")
    ap.add_argument("--keep-schema", action="store_true",
                    help="don't drop the test schema at the end (lets you inspect leads in Supabase)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return asyncio.run(run_stress(
        min_target=args.target,
        max_companies=args.max_companies,
        max_multiplier=args.max_multiplier,
        log_path=Path(args.log),
        keep_schema=args.keep_schema,
    ))


if __name__ == "__main__":
    sys.exit(main())
