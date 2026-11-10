"""Command line entry point: run a campaign, export leads, manage suppressions."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from gtm_engine.config import load_defaults, load_settings, resolve_campaign
from gtm_engine.export.csv_export import export_path, write_csv
from gtm_engine.models import CompanyType
from gtm_engine.outreach.cli import add_outreach_parser
from gtm_engine.pipeline import Pipeline
from gtm_engine.scraping.browser import build_fetcher
from gtm_engine.storage.database import Database


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _make_progress(db: Database, campaign_id: str):
    def _progress(stage: str, done: int, total: int, message: str) -> None:
        suffix = f"{done}/{total}" if total else str(done)
        print(f"[{stage:>8}] {suffix:<9} {message}", file=sys.stderr, flush=True)
        # Written to run_progress so /campaigns/{id}/progress (served from a different
        # process on Vercel) can show live state while this runs in GitHub Actions.
        db.set_run_progress(campaign_id, None, stage, done, total, message)
    return _progress


async def _run(args: argparse.Namespace) -> int:
    settings = load_settings()
    _setup_logging(args.log_level or settings.log_level)
    defaults = load_defaults()
    campaign = resolve_campaign(args.campaign, settings.database_url)
    if args.max_companies:
        campaign.max_companies = args.max_companies
    if args.min_outreach_ready is not None:
        campaign.min_outreach_ready = args.min_outreach_ready
    db = Database(settings.database_url)
    # The pipeline runs its per-company DB writes off the event loop in worker threads; a psycopg
    # connection is not safe for concurrent use, so progress writes (which fire on the main thread
    # from the same run) get their OWN connection rather than sharing the pipeline's.
    progress_db = Database(settings.database_url, ensure_schema=False)
    try:
        async with build_fetcher(settings) as fetcher:
            pipeline = Pipeline(settings, defaults, db, fetcher)
            result = await pipeline.run(campaign, progress=_make_progress(progress_db, campaign.campaign_id))
    except Exception as exc:
        progress_db.set_run_progress(campaign.campaign_id, None, "failed", 0, 0, str(exc))
        progress_db.close()
        # F3: the run is over and will never be billed, so hand the held credits straight
        # back instead of making the user wait out the reservation TTL.
        db.release_reservation(campaign.campaign_id, "refunded")
        db.close()
        raise
    progress_db.set_run_progress(campaign.campaign_id, result.run_id, "completed", 0, 0, "done")
    progress_db.close()
    # P2 (2026-10-10): credits are billed at run completion - 1 credit per outreach_ready
    # lead actually returned, not per lead attempted. Local/operator campaigns (no owner_id)
    # aren't tracked - there's no tier to bill against.
    owner_id = db.campaign_owner(campaign.campaign_id)
    if owner_id:
        db.consume_credits(owner_id, result.stats.outreach_ready)
    # F3: bill first, then drop the hold the API took at dispatch. Doing it in this order
    # means a crash between the two leaves the hold in place (self-healing via TTL) rather
    # than freeing budget for a run that was already charged.
    db.release_reservation(campaign.campaign_id, "consumed")
    if getattr(fetcher, "fallbacks", 0):
        print(f"  browser fallback rendered {fetcher.fallbacks} page(s)")

    s = result.stats
    print(f"\nrun {result.run_id} finished")
    print(f"  discovered {s.discovered} -> {s.after_dedupe} unique; processed {s.processed}")
    print(f"  BUYER {s.buyer} | VENDOR {s.vendor} | UNKNOWN {s.unknown}")
    print(f"  qualified {s.qualified} | outreach-ready {s.outreach_ready} | no website {s.no_website} | unreachable {s.unreachable} | duplicates {s.duplicates} | errors {s.errors}")

    qualified = [l for l in result.leads if l.company_type == CompanyType.BUYER and l.total_score >= campaign.min_score]
    out_q = write_csv(qualified, export_path(settings.export_dir, campaign.campaign_id, result.run_id, True))
    out_all = write_csv(result.leads, export_path(settings.export_dir, campaign.campaign_id, result.run_id, False))
    print(f"  qualified CSV: {out_q}")
    print(f"  full CSV:      {out_all}")
    db.close()
    return 0


def _export(args: argparse.Namespace) -> int:
    settings = load_settings()
    db = Database(settings.database_url)
    leads = db.list_leads(args.campaign_id, run_id=args.run_id,
                          min_score=None if args.all else args.min_score,
                          company_type=None if args.all else CompanyType.BUYER.value)
    out = Path(args.out) if args.out else export_path(settings.export_dir, args.campaign_id, args.run_id or "latest", not args.all)
    write_csv(leads, out)
    print(f"wrote {len(leads)} leads -> {out}")
    db.close()
    return 0


def _sheets(args: argparse.Namespace) -> int:
    from gtm_engine.export import sheets as sheets_export
    settings = load_settings()
    db = Database(settings.database_url)
    leads = db.list_leads(args.campaign_id, min_score=None if args.all else args.min_score,
                          company_type=None if args.all else CompanyType.BUYER.value)
    db.close()
    try:
        info = sheets_export.export_leads(leads, args.campaign_id, tab=args.tab)
    except Exception as exc:  # noqa: BLE001 - the Sheets mirror is optional; report cleanly, don't dump a traceback
        print(f"sheets export skipped: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {info['rows']} leads -> {info['url']} (tab {info['tab']})")
    return 0


def _suppress(args: argparse.Namespace) -> int:
    settings = load_settings()
    db = Database(settings.database_url)
    kind = "email" if "@" in args.value else "domain"
    db.add_suppression(args.value, kind, args.reason)
    print(f"suppressed {kind}: {args.value}")
    db.close()
    return 0


def _runs(args: argparse.Namespace) -> int:
    settings = load_settings()
    db = Database(settings.database_url)
    for r in db.list_runs(args.campaign_id):
        print(f"{r['run_id']}  {r['campaign_id']}  {r['status']:<9}  {r['started_at'][:19]}  {r.get('stats_json') or ''}")
    db.close()
    return 0


async def _nl(args: argparse.Namespace) -> int:
    """Parse natural-language text into a campaign, optionally run it."""
    from gtm_engine.campaign.nl_parser import build_campaign_from_nl
    from gtm_engine.llm.client import build_llm

    settings = load_settings()
    _setup_logging(args.log_level or settings.log_level)
    db = Database(settings.database_url)
    existing = {r["campaign_id"] for r in db.list_campaigns()}

    llm = build_llm(settings) if settings.enable_llm else None
    cfg, explanation = await build_campaign_from_nl(args.text, llm=llm, existing_ids=existing)

    print("\n--- Interpreted Campaign ---")
    print(f"  campaign_id:  {cfg.campaign_id}")
    print(f"  offer:        {cfg.offer}")
    print(f"  cities:       {cfg.geography.cities}")
    print(f"  industries:   {cfg.target_industries}")
    print(f"  exclusions:   {cfg.negative_keywords}")
    print(f"  chains:       {'excluded' if cfg.exclude_chains else 'included'}")
    print(f"  max_companies:{cfg.max_companies}")
    print(f"  hard_filters: {cfg.hard_filters}")
    print(f"  sectors:      {explanation.get('sectors_matched', [])}")
    print(f"  places:       {explanation.get('enable_places', False)}")

    if not args.yes:
        answer = input("\nRun this campaign? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("aborted")
            db.close()
            return 0

    db.upsert_campaign(cfg.campaign_id, cfg.name, cfg.model_dump(mode="json"))

    defaults = load_defaults()
    progress_db = Database(settings.database_url, ensure_schema=False)
    try:
        async with build_fetcher(settings) as fetcher:
            pipeline = Pipeline(settings, defaults, db, fetcher)
            result = await pipeline.run(cfg, progress=_make_progress(progress_db, cfg.campaign_id))
    except Exception as exc:
        progress_db.set_run_progress(cfg.campaign_id, None, "failed", 0, 0, str(exc))
        progress_db.close()
        db.release_reservation(cfg.campaign_id, "refunded")  # F3, see _run above
        db.close()
        raise
    progress_db.set_run_progress(cfg.campaign_id, result.run_id, "completed", 0, 0, "done")
    progress_db.close()
    owner_id = db.campaign_owner(cfg.campaign_id)
    if owner_id:
        db.consume_credits(owner_id, result.stats.outreach_ready)
    db.release_reservation(cfg.campaign_id, "consumed")  # F3, see _run above

    s = result.stats
    print(f"\nrun {result.run_id} finished")
    print(f"  discovered {s.discovered} -> {s.after_dedupe} unique; processed {s.processed}")
    print(f"  BUYER {s.buyer} | VENDOR {s.vendor} | UNKNOWN {s.unknown}")
    print(f"  qualified {s.qualified} | outreach-ready {s.outreach_ready}")
    if s.places_api_calls:
        print(f"  Google Places API: {s.places_api_calls} calls")
    if s.hard_filtered:
        print(f"  hard-filtered {s.hard_filtered} leads")

    qualified = [l for l in result.leads if l.company_type == CompanyType.BUYER and l.total_score >= cfg.min_score]
    out_q = write_csv(qualified, export_path(settings.export_dir, cfg.campaign_id, result.run_id, True))
    out_all = write_csv(result.leads, export_path(settings.export_dir, cfg.campaign_id, result.run_id, False))
    print(f"  qualified CSV: {out_q}")
    print(f"  full CSV:      {out_all}")
    db.close()
    return 0


def _campaign_id(args: argparse.Namespace) -> int:
    """Print the campaign_id for a YAML path or a DB id, so a workflow can name its output
    folder without re-parsing the input (which fails when the input is an id, not a file)."""
    settings = load_settings()
    print(resolve_campaign(args.campaign, settings.database_url).campaign_id)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gtm", description="Buyer-only GTM lead engine")
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run a campaign end-to-end and export CSVs")
    run.add_argument("campaign", help="campaign YAML path, or a campaign_id stored in the DB")
    run.add_argument("--max-companies", type=int, default=None)
    run.add_argument("--min-outreach-ready", type=int, default=None,
                     help="guaranteed floor of qualified+outreach-ready leads; "
                          "unset defaults to ceil(max_companies/3), 0 disables expansion")
    run.add_argument("--log-level", default=None)
    run.set_defaults(func=lambda a: asyncio.run(_run(a)))

    exp = sub.add_parser("export", help="export stored leads for a campaign")
    exp.add_argument("campaign_id")
    exp.add_argument("--run-id", default=None)
    exp.add_argument("--min-score", type=int, default=70)
    exp.add_argument("--all", action="store_true", help="include vendors/unknown/rejected")
    exp.add_argument("--out", default=None)
    exp.set_defaults(func=_export)

    sh = sub.add_parser("sheets", help="mirror leads to the configured Google Sheet")
    sh.add_argument("campaign_id")
    sh.add_argument("--min-score", type=int, default=70)
    sh.add_argument("--all", action="store_true")
    sh.add_argument("--tab", default=None)
    sh.set_defaults(func=_sheets)

    sup = sub.add_parser("suppress", help="never contact this email or domain again")
    sup.add_argument("value")
    sup.add_argument("--reason", default=None)
    sup.set_defaults(func=_suppress)

    runs = sub.add_parser("runs", help="list past runs")
    runs.add_argument("--campaign-id", default=None)
    runs.set_defaults(func=_runs)

    cid = sub.add_parser("campaign-id", help="print the campaign_id for a YAML path or DB id")
    cid.add_argument("campaign", help="campaign YAML path, or a campaign_id stored in the DB")
    cid.set_defaults(func=_campaign_id)

    nl = sub.add_parser("nl", help="create and run a campaign from natural-language text")
    nl.add_argument("text", help="free-form description of what you want to find")
    nl.add_argument("--yes", "-y", action="store_true", help="skip confirmation prompt")
    nl.add_argument("--log-level", default=None)
    nl.set_defaults(func=lambda a: asyncio.run(_nl(a)))

    add_outreach_parser(sub)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
