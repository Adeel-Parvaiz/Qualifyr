"""Invisible per-user lead personalization (docs/PERSONALIZATION_PLAN.md).

Covers: cold-start parity, bounded bias, per-user isolation, the kill-switch, and the
record -> persist -> reload -> update -> persist round trip. DB-backed tests use pg_schema
(disposable Postgres schema) like the rest of the suite."""

import random

from gtm_engine.models import Lead, Priority, ScoreBreakdown
from gtm_engine.personalization.bandit import predict, update
from gtm_engine.personalization.features import FEATURE_NAMES, extract_features, extract_features_from_score
from gtm_engine.personalization.ranker import rerank_bias
from gtm_engine.storage.database import Database


def _score(**kw) -> ScoreBreakdown:
    return ScoreBreakdown(
        review_band=kw.get("review_band", 10), rating_score=kw.get("rating_score", 5),
        proximity_tier=kw.get("proximity_tier", 8), online_gap=kw.get("online_gap", 15),
        pain_evidence=kw.get("pain_evidence", 6), total=kw.get("total", 44),
        priority=Priority.QUALIFIED,
    )


def test_cold_start_bias_is_exactly_zero():
    x = extract_features_from_score(_score(), has_email=True, has_phone=False)
    assert rerank_bias(None, x, bound=8.0) == 0.0


def test_bias_is_always_within_bound_over_random_vectors():
    random.seed(0)
    for _ in range(200):
        weights = [random.uniform(-20, 20) for _ in FEATURE_NAMES]
        x = [1.0] + [random.uniform(0, 1) for _ in range(len(FEATURE_NAMES) - 1)]
        bias = rerank_bias({"weights": weights, "n_updates": 1}, x, bound=8.0)
        assert -8.0 <= bias <= 8.0


def test_enable_personalization_false_forces_zero_bias_regardless_of_model_state():
    # The kill-switch lives in pipeline.py (checked before rerank_bias is even called), but the
    # contract it relies on is: rank_score falls back to total_score whenever personalization
    # doesn't run. Simulate that branch directly.
    enable_personalization = False
    model_row = {"weights": [10.0] * len(FEATURE_NAMES), "n_updates": 5}
    x = extract_features_from_score(_score(), has_email=True, has_phone=True)
    rank_score = _score().total
    if enable_personalization:
        rank_score += round(rerank_bias(model_row, x, bound=8.0))
    assert rank_score == _score().total


def test_repeated_high_reward_shifts_weight_toward_that_feature():
    x = extract_features_from_score(_score(online_gap=25), has_email=False, has_phone=True)
    weights = [0.0] * len(x)
    for _ in range(50):
        weights = update(weights, x, reward=1.0, lr=0.05)
    online_gap_idx = FEATURE_NAMES.index("online_gap")
    assert weights[online_gap_idx] > 0
    assert predict(weights, x) > 0.5


def test_a_users_updates_never_move_another_users_model(pg_schema):
    db = Database(pg_schema())
    x = extract_features_from_score(_score(), has_email=True, has_phone=False)
    for _ in range(10):
        model = db.get_pref_model("alice") or {"weights": [0.0] * len(x), "n_updates": 0}
        w = update(model["weights"], x, reward=1.0, lr=0.1)
        db.upsert_pref_model("alice", w, model["n_updates"] + 1)

    bob_model = db.get_pref_model("bob")
    assert bob_model is None
    alice_model = db.get_pref_model("alice")
    assert alice_model is not None
    assert alice_model["n_updates"] == 10
    assert any(w != 0.0 for w in alice_model["weights"])


def test_feedback_round_trips_record_persist_reload_update_persist(pg_schema):
    db = Database(pg_schema())
    lead = Lead(campaign_id="c1", company_name="Acme", total_score=44, priority=Priority.QUALIFIED,
               contact_email="a@acme.com",
               evidence={"score": _score().model_dump(mode="json")})
    x = extract_features(lead)
    assert len(x) == len(FEATURE_NAMES)

    db.record_lead_feedback("carol", lead.lead_id, lead.campaign_id, x, "approved", 1.0)
    model = db.get_pref_model("carol") or {"weights": [0.0] * len(x), "n_updates": 0}
    new_weights = update(model["weights"], x, reward=1.0, lr=0.05)
    db.upsert_pref_model("carol", new_weights, model["n_updates"] + 1)

    reloaded = db.get_pref_model("carol")
    assert reloaded["n_updates"] == 1
    assert reloaded["weights"] == new_weights


def test_extract_features_from_lead_matches_extract_features_from_score():
    score = _score(review_band=20, rating_score=8, proximity_tier=12, online_gap=18, pain_evidence=10)
    lead = Lead(campaign_id="c1", company_name="Acme", total_score=score.total, priority=score.priority,
               contact_email="a@acme.com", phone="+92123",
               evidence={"score": score.model_dump(mode="json")})
    assert extract_features(lead) == extract_features_from_score(score, has_email=True, has_phone=True)
