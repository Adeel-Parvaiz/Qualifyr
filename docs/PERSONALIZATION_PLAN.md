# Invisible Per-User Lead Personalization — Implementation Plan

**Status:** drafted 2026-10-10, **not started**. Gated on: the 10-campaign before/after
verification confirming leads are landing correctly (qualified / high-priority counts vs the
12-outreach-ready / 463-crawled baseline). Do not begin coding until that gate passes.

**Owner direction (verbatim intent):** every user should get leads tuned to *their* wants. A
model works in the background, is **never visible** to the user, keeps a **separate memory per
person in the database**, gets better over time, and self-corrects when it gets something wrong —
so that high-yield, high-priority leads surface first for each individual searcher.

---

## 1. Research basis (what we evaluated, 2026-10-10)

| Option | What it is | Verdict for us |
|---|---|---|
| **SalesLoop** (arXiv 2607.20655) | RL-from-outcomes for lead ranking; Discriminative GRPO listwise objective; reward = conversion weighted by rank position + velocity. Reported 2.3× conversion vs human baseline over a 160-day A/B test. | **Right thesis, wrong weight class for v1.** GRPO listwise RL needs large volume + an A/B harness to tune safely. Overfits at pilot scale. Adopt as the **v2 north star**; our v1 feedback schema feeds it with no migration. |
| **Vowpal Wabbit** contextual bandit (`learn_to_pick`) | Production-grade online CB, propensity-weighted, incremental. | Excellent fit, but a **compiled C++ dependency**. Deferred: owner chose pure-Python first (engine is otherwise pure-Python/psycopg, deploys on Vercel/Actions). |
| **LightFM** (WARP hybrid recommender) | Metadata-embedding cold-start (a new user = sum of feature embeddings, not a blank slate). | Repo looks stale (~3+ yr). **Borrow the cold-start idea, not the dependency.** |
| **Inspector** (OSS revenue intel) | Buying-signal detection from CRM/PostHog. | Not a learning model; out of scope. |

**Decision:** pure-Python per-user **logistic contextual bandit** as a bounded re-rank bias,
with a SalesLoop-ready feedback schema so a listwise-RL upgrade is a drop-in later.

---

## 2. Core design principle (non-negotiable, respects CLAUDE.md rules)

The deterministic `score.total` and `score.priority` produced by
[`score_lead()`](../gtm_engine/scoring/scoring.py) (returns `ScoreBreakdown` at
`scoring/scoring.py:203`) are **never modified**. The model produces only a **bounded additive
re-rank bias** used purely for *ordering/surfacing within an already-qualified set*:

- It **cannot invent a score** — grounded total is preserved and still displayed/audited.
- It **cannot cross a routing threshold** — bias is clamped to `±personalization_max_bias`
  (default 8) and applied to a *separate* `rank_score`, not to `total_score`; priority bands
  (`high_priority=55 / qualified=40 / review=20`) are decided by the untouched deterministic total.
- **Cold-start = zero bias = today's exact behavior.** A user with no history, or any failure in
  the personalization path, ranks identically to the current engine. This is the mandated
  "deterministic fallback always runs."

Net effect: personalization reorders *which qualified leads a given user sees first*, never
fabricates or promotes leads the grounded engine wouldn't have qualified.

---

## 3. How it learns (the per-user memory)

- **Model:** per-user logistic bandit, weight vector `w` (one row per user in the DB = the memory).
- **Context `x`:** the lead's feature vector — the 5 score dimensions already computed
  (`review_band, rating, proximity_tier, online_gap, pain_evidence`, normalized) plus cheap
  one-hots (industry bucket, city, contact-type: email / phone / both / none). **Zero new
  enrichment cost** — every feature is already available at scoring time.
- **Prediction:** `p = sigmoid(w · x)` → "will this user act on this lead?" → mapped to a bounded bias.
- **Reward (owner-chosen: the searcher's own in-app behavior, dense + immediate):**
  `approved / exported / contacted = 1`, `skipped / dismissed = 0`. This directly optimizes the
  **search experience for the person searching**, which is the stated goal. Downstream signals
  (reply, meeting) are *accepted by the schema* but **not required** for v1.
- **Update:** one online SGD step per feedback event — `w ← w + lr · (reward − p) · x`. This is
  the "gets better over time, and when a wrong thing happens makes it more adapted" behavior,
  without a live exploration policy that could visibly misfire mid-campaign.
- **Isolation:** strictly per `user_id`. No cross-user leakage. One person's corrections never
  move another person's model.

---

## 4. Phases

### Phase 1 — Feedback schema (the SalesLoop-ready foundation)
The single most important decision: **log the raw feature vector + outcome**, not just a label,
so the v2 listwise/RL upgrade needs no migration.

- New table `lead_feedback(id, user_id, lead_id, campaign_id, feature_vector jsonb, action text,
  reward real, created_at timestamptz)`.
- New table `user_pref_model(user_id text primary key, weights jsonb, n_updates int,
  updated_at timestamptz)` — the per-person memory.
- `gtm_engine/storage/database.py`: `record_lead_feedback(...)`, `get_pref_model(user_id)`,
  `upsert_pref_model(user_id, weights, n_updates)`. Follow the existing `psycopg` +
  `INSERT ... ON CONFLICT DO UPDATE` style already used by `consume_credits`.
- Schema migration applied the same way the project already manages Supabase tables (match the
  existing pattern; no new migration framework).

### Phase 2 — The model (`gtm_engine/personalization/`, new package)
- `features.py`: `extract_features(inputs: ScoreInputs, score: ScoreBreakdown) -> np.ndarray` +
  a stable, versioned feature-name list (so stored vectors remain interpretable across releases).
- `bandit.py`: `LogisticBandit` — `predict(w, x) -> float`, `update(w, x, reward, lr) -> w`.
  Pure NumPy. Deterministic. No I/O.
- `ranker.py`: `rerank_bias(model_row, x, bound) -> float` → `(p − 0.5) · 2 · bound`, clamped to
  `±bound`. Returns `0.0` when `model_row is None` (cold start) — the fallback guarantee.

### Phase 3 — Pipeline + API wiring
- `gtm_engine/pipeline.py`, right after `score_lead(...)` (currently `pipeline.py:922`): if the
  campaign owner has a pref model, compute `rank_score = score.total + rerank_bias(...)`. Store on
  a **new `Lead.rank_score` field** used only for result ordering / surfacing order. Leave
  `total_score` and `priority` exactly as they are. When no owner / no model → `rank_score =
  score.total`.
- Result ordering (API list + CSV + Sheets mirror) sorts by `rank_score` within priority band,
  then by `total_score` as the deterministic tiebreak.
- New endpoint `POST /leads/{id}/feedback` (body: `action ∈ {approved, skipped, exported,
  contacted}`): resolves the lead's stored feature vector, writes a `lead_feedback` row, runs one
  `LogisticBandit.update`, and upserts the user's model. This is the invisible learning loop.
- `gtm_engine/config/schema.py`: add `personalization_max_bias: float = 8.0`,
  `personalization_learning_rate: float = 0.05`, `enable_personalization: bool = True`
  (kill-switch → bias forced to 0, exact current behavior).
- **Frontend: no visible change.** Wire the *existing* approve / skip / export / contact actions
  in `web/` to fire the feedback call. The user never sees a model, a toggle, or a score shift —
  per the "never visible" requirement. Verify via `cd web && npm run build` (no dev server).

### Phase 4 — Tests + verification
- `tests/test_personalization.py`:
  - cold-start returns exactly `0.0` bias → `rank_score == total_score` (parity with today).
  - bias is always within `±personalization_max_bias` (property test over random vectors).
  - a user who repeatedly approves high-`online_gap` leads shifts *that user's* weights up on the
    online-gap feature; a different user's model is unaffected (isolation).
  - `enable_personalization=False` forces zero bias regardless of model state.
  - feedback round-trips: record → persist → reload → update → persist.
- Full regression: `pytest -q` (expect the known 4 `test_load_concurrency.py` pooler failures to
  remain the only failures — infra, not us). `cd web && npm run build`.

---

## 5. Files touched

| File | Phase | Change |
|---|---|---|
| `gtm_engine/storage/database.py` | 1 | 2 tables + 3 methods (`record_lead_feedback`, `get_pref_model`, `upsert_pref_model`) |
| `gtm_engine/personalization/features.py` | 2 | **NEW** — feature extraction (reuses computed score dims) |
| `gtm_engine/personalization/bandit.py` | 2 | **NEW** — pure-NumPy logistic bandit |
| `gtm_engine/personalization/ranker.py` | 2 | **NEW** — bounded re-rank bias |
| `gtm_engine/pipeline.py` | 3 | compute `rank_score` after `score_lead` (line ~922); order by it |
| `gtm_engine/models.py` | 3 | add `Lead.rank_score` field |
| `gtm_engine/api/main.py` | 3 | `POST /leads/{id}/feedback`; order list endpoints by `rank_score` |
| `gtm_engine/config/schema.py` | 3 | `personalization_max_bias`, `_learning_rate`, `enable_personalization` |
| `web/` (lead actions) | 3 | fire feedback call on existing approve/skip/export/contact — no new UI |
| `tests/test_personalization.py` | 4 | **NEW** — parity, bounds, learning, isolation, persistence |

---

## 6. Rollout & safety

- Ships **on** (`enable_personalization=True`) but **inert until a user generates feedback** —
  every user starts at zero bias = current engine. No big-bang behavior change.
- `enable_personalization=False` is a complete kill-switch (bias → 0) if anything looks off.
- `rank_score` is observable alongside `total_score`, so any reordering is auditable — we can
  always see the grounded score next to the personalized rank.
- Grounded-only rule intact: the model reorders, it never invents or promotes past the thresholds.

---

## 7. Deferred to v2 (explicitly not built now)

SalesLoop-style listwise/GRPO RL with conversion-velocity reward. The Phase-1 `lead_feedback`
table already stores raw feature vectors + outcomes + timestamps, so v2 trains on the exact data
v1 collects — **no migration, pure upgrade** — once per-user / cross-user volume justifies the
added complexity and an A/B harness exists to tune it safely.
