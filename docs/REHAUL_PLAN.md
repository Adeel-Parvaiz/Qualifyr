# REHAUL PLAN — 2026-10-10

Single source of truth for the two CEO directives executed in the 2026-10-10 session:

1. **CEO audit** (voice message #1) — "generated filler" review across 6 areas
2. **CEO pricing overhaul** (voice message #2) — tier structure, credits, personalization

**All work lands on `origin` (Hasee10) only. `grydinteam` is untouched until explicit CEO sign-off after end-to-end testing.**

---

## Status tracker

Updated after every item ships. If this file is ahead of main (uncommitted), the item is
in-flight.

| # | Item | Status | Commit |
|---|---|---|---|
| plan | REHAUL_PLAN.md | ✅ done | `(this commit)` |
| A1 | Delete Foursquare | ✅ done | `4437fc7` |
| A2 | Hide 5 global registries behind `GTM_UNLOCK_GLOBAL` | ✅ done | `19c4de5` |
| A3 | OSM warn-and-continue when Overpass fails | ✅ done | `cdfe6d6` |
| B1 | Per-lead `outreach skip` log at every drop point | ✅ done | `2873c05` |
| B2 | `Lead.outreach_skip_reason` field + CSV column | ✅ done | `2873c05` |
| B3 | `SequenceStatus.NEEDS_CONTACT` for qualified-no-contact | ✅ done | `2873c05` |
| C1 | `evidence["intent_judge_degraded"]` on LLM 429 | ✅ done | `c029369` |
| P4 | Three tiers as data (`gtm_engine/pricing/tiers.py`) | ✅ done | `4058f28` |
| P3 | Hard `max_leads_hard_cap` + remove multiplier knob | ✅ done | `4058f28` |
| P5 | Deterministic personalization only (`llm=None` in `generate_pitch_angle`)* | ✅ done | `4058f28` |
| P1 | Per-run `leads_per_run` dropdown (3/5/10/…) | ✅ done | `9095375` |
| P2 | Credit-based accounting (`credits_consumed`) | ✅ done | `b31ab2c` |
| P6 | Document multi-email key resolution | ✅ done | `97ceee0` |
| test | End-to-end campaign before/after | ⏳ pending | — |
| F1 | Bill only for delivered leads (recompute after hard filters) | ✅ done | `(this commit)` |
| F2 | Tier-aware monthly runs cap (paid tiers were capped at 20–50%) | ✅ done | `(this commit)` |
| F3 | Reserve credits at dispatch (gate/consume TOCTOU) | ✅ done | `(this commit)` |
| F4 | Surface `NEEDS_CONTACT` / skip reasons in the Leads UI | ⬜ open | — |

---

## Post-ship audit — 2026-10-11

A read-only audit of all 14 items above (verification-only, no edits) confirmed A1, A2, A3,
B1, B2, C1 are genuinely implemented, not cosmetic. It also found four gaps that only exist
on a paying user's path — which is why none had been caught: **no paid account has ever
existed** (tier is set by direct DB write; the `test` row above is still pending).

**F1 — users were billed for leads they never received.** `_tally()` increments
`stats.outreach_ready` per company during the expansion rounds; `_apply_hard_filters()` then
drops leads from the returned set; `cli.py` bills that stale pre-filter counter. Any campaign
with hard filters overbilled by up to `stats.hard_filtered`, and NL campaigns set hard filters
routinely ("businesses lacking online presence"). Fixed by recomputing `stats.outreach_ready`
from the delivered list before `finish_run()`. Guarded by `tests/test_billing_invariant.py`,
which asserts the recompute sits between the filter and the persist.

**F2 — paid tiers could not spend what they bought.** `MONTHLY_LIMITS["runs"]` was a flat
`5`/month for every account, predating the tier system; its own comment justified this with
"no billing/tier system yet to tell a paid account apart from a free one", which went stale
when P4 landed `resolve_user_tier()`. Enterprise could reach 500 of 2,500 credits (20%), Pro
250 of 500 (50%). The runs cap is now derived from the tier's credits at its *smallest* run
size, so the runs counter can never bind before credits do (all three tiers now reach 100%).
Precedence is unchanged: user preference → operator env var → default. The daily runs limit
and `tier.daily_credit_throttle` remain the burst controls.

**F3 — credits are now reserved at dispatch, not just gated.** The pre-run gate ran in the API
at dispatch while consumption happened minutes-to-hours later in the Actions runner, so
concurrent dispatches across different campaigns all read the same pre-consumption balance and
together overspent the tier. This was *masked* by the flat runs cap until F2 removed the mask.

Semantics (CEO, 2026-10-11): **reserve the requested size, refund on a recorded failure, 24h
TTL reconciliation.** A `credit_reservations` row is written in `run_campaign` after the gate
passes and before `dispatch_workflow`; `credit_status()` counts active holds as used, so the
next gate immediately sees the in-flight cost. The runner releases its own hold in `cli.py` —
`consumed` after billing, `refunded` in the failure path — and the API refunds directly if the
dispatch call itself raises. `reserved_credits()` ignores holds older than
`CREDIT_RESERVATION_TTL_HOURS` (24), so a runner that is killed and never reports back cannot
strand a user's credits; `expire_stale_reservations()` marks those rows for operator
visibility but correctness does not depend on it running.

Reservations are keyed by **campaign**, not by an id threaded through the Actions workflow:
`run_campaign` already refuses to dispatch while a run is in progress for that campaign, so
there is at most one active hold per campaign. That kept `gather-leads.yml` inputs and the
`gtm run` CLI signature unchanged. Billing is deliberately ordered *before* the release, so a
crash between the two leaves the hold in place (self-healing via TTL) rather than freeing
budget for a run that was already charged. 13 tests in `tests/test_credit_reservations.py`.

**F4 — B3's operator visibility stops at the CSV (open).** `outreach_skip_reason` and
`SequenceStatus.NEEDS_CONTACT` are persisted and exported, but there are zero references to
either in `web/src` — no Leads filter, no badge. B3's stated purpose was that the operator can
*see and act on* qualified-but-unreachable leads; today that only works if they open the CSV.

\* P5 as landed is partial: only the `llm=None` call-site change shipped (forces the
existing deterministic fallback path). The fuller P5 scope in the plan below — deleting
`generate_pitch_angle`/`build_research_brief` from `llm/tasks.py` and dropping
`pitch_angle` from CSV exports — has not been done yet.

---

## 1. Audit items (CEO voice message #1)

### A1 — Delete Foursquare

Dataset dead upstream (S3 bucket stripped), code fails soft and yields 0 across every PK
campaign. 125 LOC + 93 LOC of tests to maintain for no return.

**Changes:**
- Delete `gtm_engine/discovery/foursquare.py`
- Delete `tests/test_foursquare.py`
- Delete `foursquare_categories` field from `CampaignConfig`
- Delete `FoursquareDiscovery` import + wiring in `pipeline.discover()`
- Update `docs/API_KEYS.md`: remove the "Foursquare" section
- Update `scripts/stress_test_100.py`: drop foursquare_categories from the test campaign
- Update `scripts/internal_test_batch.py`: drop foursquare_categories from campaigns

**Why hard-delete not soft-disable:** Per CEO's #3 ("don't just comment things out"), and the
dataset isn't coming back without opting into Foursquare's gated Places Portal — a product
decision that would get its own planning.

**Verification:** `pytest -q` + `cd web && npm run build`. Expect ~450 tests still pass
(down from 460).

### A2 — Hide 5 global registries behind `GTM_UNLOCK_GLOBAL`

GLEIF / Wikidata / EDGAR / Companies House / GLEIF Golden Copy are wired but have never fired
in any PK campaign (they require country-code + registry-code fields that no PK campaign
sets). Dormant surface for the active (PK-only) use case.

**Changes:**
- In `pipeline.discover()`, wrap the 5 source blocks with:
  ```python
  if os.environ.get("GTM_UNLOCK_GLOBAL") == "1":
      # existing GLEIF/Wikidata/EDGAR/CompaniesHouse/GLEIFGoldenCopy block
  ```
- When the flag is off AND a campaign has `gleif_lei_queries` / `wikidata_industries` / etc
  set, log one `log.warning("global discovery sources are off; set GTM_UNLOCK_GLOBAL=1 to use them")`.
- Keep all tests passing (the source classes themselves are untouched).

**Verification:** All existing tests pass with `GTM_UNLOCK_GLOBAL` unset (sources skip);
tests for the 5 sources themselves still pass (they test the classes directly, not the
pipeline wiring).

### A3 — OSM warn-and-continue

Overpass mirrors return 500/504 for Pakistani queries most of the time. The 24-min delay in
yesterday's sanity run was 90% Overpass retries. OSM contributes near-zero signal when
Overture carries 1,721+1,372 places for Karachi+Lahore.

**Changes:**
- In `gtm_engine/discovery/osm.py`, after all mirror attempts fail, emit
  `log.warning("osm: all mirrors unreachable for %s; skipping without retry", area)` and
  move on **without the 2× timeout multiplier**. (Current behavior retries each mirror up
  to 3× — total ~90s per failing area.)
- Add `osm_unavailable: bool` field to `RunStats` so operators can see OSM was skipped.
- No change to OSMDiscovery output structure.

**Verification:** Existing OSM tests still pass (they mock the Overpass response). Add one
new test for "all mirrors fail → stats.osm_unavailable=True, no exception raised".

### B1 — Per-lead `outreach skip` log

Qualified leads that don't become outreach-ready are silently dropped. Only aggregate
counters (`stats.suppressed`, `stats.no_contact`) are logged.

**Changes:**
- In `gtm_engine/scoring/scoring.py:is_outreach_ready`, change the return to also return the
  reason: `(bool, str | None)`. Reasons: `"not_a_buyer"`, `"score_below_min"`,
  `"priority_below_qualified"`, `"email_status_unusable"` (with no phone).
- At the two caller sites (`pipeline.process_company`, `outreach/sequencer.eligible`),
  log: `log.info("outreach skip: lead=%s campaign=%s reason=%s", lead_id, campaign_id, reason)`.
- Suppression drops at pipeline+sequencer get their own log line too.

### B2 — `Lead.outreach_skip_reason`

Persist B1's reason on the Lead so a reviewer can filter by it in the CSV.

**Changes:**
- Add `outreach_skip_reason: str | None = None` to `Lead` (models.py). No DB migration
  needed — `data_json` is the storage.
- Add "Outreach skip reason" column to `CLEAN_COLUMNS` (and `CSV_COLUMNS`).
- Set it in `pipeline.process_company` when `outreach_ready=False`.

### B3 — `SequenceStatus.NEEDS_CONTACT`

CEO wants every qualified lead to get outreach OR a logged skip reason. For qualified leads
with no verified contact, "silently excluded" is the wrong default — they should land in a
new queue state the operator can see and act on.

**Changes:**
- Add `NEEDS_CONTACT = "needs_contact"` to `SequenceStatus` enum.
- Qualified lead (priority ∈ {high_priority, qualified}) with no usable email and no phone
  → `outreach_ready=False` AND `sequence_status=NEEDS_CONTACT` (not just NOT_QUEUED).
- Sequencer never auto-sends NEEDS_CONTACT; operator can promote to QUEUED after manually
  adding contact.
- Add a frontend filter/view for "needs contact" leads on the Leads page (follow-up, not
  blocking).

### C1 — Mark leads when LLM intent judge is throttled

Groq 429s have been degrading scores silently in the stress run. Operator sees low scores
without knowing the judge couldn't run.

**Changes:**
- In `gtm_engine/pipeline.py` around the `judge_intent` call, catch LLM failures and set
  `evidence["intent_judge_degraded"] = "<provider>_rate_limited"` on the lead.
- One `log.warning("intent judge degraded: %s, lead=%s", exc, lead_id)` per occurrence.
- Lead still ships; the operator just knows the score is a floor, not a ceiling.

---

## 2. Pricing items (CEO voice message #2)

### P4 — Three tiers as data (do this first; everything else references it)

Move the scattered `FREE_MAX_CAMPAIGNS` / `FREE_MAX_LEADS_PER_CAMPAIGN` env flags into a
single `gtm_engine/pricing/tiers.py` module:

```python
@dataclass(frozen=True)
class Tier:
    name: str                       # "free" | "pro" | "enterprise"
    price_usd_per_month: int
    monthly_credits: int            # P2
    allowed_leads_per_run: list[int]  # P1 — the dropdown choices
    max_campaigns: int
    daily_credit_throttle: int      # safety ceiling
    # Personalization is deterministic for all tiers (CEO 2026-10-10); no tier field for it.

FREE = Tier(name="free", price_usd_per_month=0, monthly_credits=30,
            allowed_leads_per_run=[3, 5, 10],
            max_campaigns=3, daily_credit_throttle=10)
PRO = Tier(name="pro", price_usd_per_month=30, monthly_credits=500,
           allowed_leads_per_run=[3, 5, 10, 25, 50],
           max_campaigns=25, daily_credit_throttle=100)
ENTERPRISE = Tier(name="enterprise", price_usd_per_month=50, monthly_credits=2500,
                  allowed_leads_per_run=[3, 5, 10, 25, 50, 100],
                  max_campaigns=100, daily_credit_throttle=250)
```

Tier assignment per user comes from a new `user_tier` column on the Supabase user profile
(default `"free"`). No Stripe integration yet — tier is operator-settable until Stripe lands.

**Backward compat:** Existing `FREE_MAX_*` env vars become overrides for the free tier only.
If set, they win over the dataclass default. One release cycle of this, then remove the
env vars.

### P3 — Hard `max_leads_hard_cap`; remove infinite expansion loop

Current `max_expansion_multiplier=10` can scrape 10× the user's `max_companies`. CEO wants
a hard stop — if a 50-lead run finds 33, stop at 33.

**Changes:**
- Add `max_leads: int` to `CampaignConfig` (the user's chosen 3/5/10/25/50/100 from P1).
- `max_leads_hard_cap = max_leads` by default — not user-settable (safety invariant).
- In `pipeline.run()`, inside the expansion loop, break as soon as
  `len(outreach_ready_leads) >= max_leads_hard_cap`.
- Remove `max_expansion_multiplier` from the UI and API input schemas. Keep the field
  internally with a hard default of 3 for safety (never user-visible).
- Rename `max_companies` → `max_leads` throughout (grep + rename; this is the user's
  mental model).

**Decision:** `1 credit = 1 lead returned` (final; see P2). "Lead returned" means a Lead
row that cleared buyer-fit, scoring, and outreach-ready gate (so `outreach_ready=True`).
Companies crawled but dropped at any gate don't count against credits — the user only pays
for actionable leads.

### P1 — Per-run `leads_per_run` dropdown

**Changes:**
- Frontend: Run-panel gets a dropdown "Leads this run: [3, 5, 10, …]" sourced from the
  user's tier's `allowed_leads_per_run`.
- API `/campaigns/{id}/run` accepts `leads_per_run: int` and validates it against the
  caller's tier. 400 if not in the tier's allowed list.
- Backend: `leads_per_run` becomes `campaign.max_leads` for that run (one-shot override).
- Default on load = the lowest tier value (3 for free).

### P2 — Credit-based accounting

The core shift: stop counting *runs* dispatched, start counting *leads returned*.

**Changes:**
- Add `credits_consumed_month: int DEFAULT 0` and `credits_reset_month: TEXT` columns to
  `usage_counts` (schema migration via `ALTER TABLE … ADD COLUMN IF NOT EXISTS`).
- New method `Database.consume_credits(user_id, amount) -> int` returning remaining credits.
  Called at run completion with `amount = len(outreach_ready_leads)`.
- Pre-run gate: before dispatching, check `credits_remaining(user_id) >= leads_per_run`.
  Reject with 402 Payment Required (or 429 for free tier) if insufficient.
- Daily throttle: `credits_consumed_today` (resets daily) caps burn rate per
  `tier.daily_credit_throttle`.
- Settings → Usage: "Credits used: 24 / 500 this month" bar; secondary "today: 24 / 100".
- `runs` counter stays in the DB for backward compat but is no longer a cap.

### P5 — Deterministic personalization only (LLM pitch+brief removed)

CEO direction (2026-10-10): "deterministic please." All tiers get the same
deterministic personalization hook (`build_personalization_hook` — signal-based, no LLM).
The LLM pitch angle and LLM research brief are **removed** from the engine entirely —
they were burning Groq quota (two calls per lead) for marginal value and were a major
contributor to the Groq 429 cascade in the 2026-10-09 stress run.

**Changes:**
- Delete the `generate_pitch_angle` + `build_research_brief` calls from
  `pipeline.process_company`.
- Keep `Lead.pitch_angle` and `Lead.research_brief` fields for backward compat (nullable;
  future-proofing in case we re-add LLM personalization behind an opt-in flag).
- Remove the two functions from `gtm_engine/llm/tasks.py` (or demote to deprecated).
- Remove `Lead.pitch_angle` from CSV columns (keep in the model, hide from exports).
- No tier-gate on personalization. The `personalization` field on `Tier` is removed.
- UI stays unchanged (the "Why it qualified" + "Signals" columns from the deterministic
  hook were always doing the heavy lifting anyway).

**Net effect:** Fewer LLM calls per lead (down from ~3 to ~1 — only the intent judge
remains). Lower Groq usage, less 429 risk. Every tier gets the same personalization.

### P6 — Multi-email key resolution

**Current behavior (documented):** Supabase auth is 1 user = 1 email. API keys are stored
in `user_api_keys(user_id, key_name)` with `(user_id, key_name) UNIQUE`. If one human has
multiple Supabase accounts (one per email), each account has its own key set — there's no
cross-email sharing.

**For the overhaul: no code change this round.** Document the current behavior in
`docs/API_KEYS.md`:

> If a single person uses multiple sign-in emails, each email is a separate Supabase user
> with its own API key set. There is no key sharing across emails. If this needs to change
> (e.g. a team of 3 shares one Groq key), the right fix is Orgs: a new `orgs` table +
> `org_members` + fallback resolution user → org → operator env var. Not scoped for this
> release.

**Future planning (not shipped this round):** If customer demand materializes, the Orgs
feature is ~2-3 days of work. Scoped out separately.

---

## 3. Verification

After every item ships (per-item `pytest -q <touched files>` + `npm run build`):
- Full `pytest -q` once at the end
- `npm run build` once at the end
- **One end-to-end campaign** against an isolated Supabase schema, same scope as yesterday's
  stress run (Karachi + Lahore clothing retail, 10 leads). Report: companies crawled,
  qualified count, outreach_ready count, credits consumed, outreach_skip_reason distribution.
- Compare against yesterday's baseline (12 outreach_ready / 463 crawled). Target: same or
  better conversion, with every skipped lead now carrying a logged reason.

## 4. Push plan

- All commits land on local `main`.
- Push `origin main` after all 14 items + verification pass.
- **grydinteam gets nothing until explicit CEO approval of the Hasee10 behavior.**
