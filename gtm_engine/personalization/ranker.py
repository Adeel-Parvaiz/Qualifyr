"""Bounded re-rank bias (docs/PERSONALIZATION_PLAN.md).

This is the only point where personalization touches scoring, and it never touches the
grounded `total_score` / `priority` - it only produces a separate, clamped `rank_score` offset
used for ordering within an already-qualified set. Cold start (no model row) = 0.0 bias =
today's exact behavior, always."""

from __future__ import annotations

from gtm_engine.personalization.bandit import predict


def rerank_bias(model_row: dict | None, x: list[float], bound: float) -> float:
    """Returns a bias in [-bound, +bound]. 0.0 when model_row is None (cold start/fallback)."""
    if model_row is None:
        return 0.0
    p = predict(model_row["weights"], x)
    bias = (p - 0.5) * 2 * bound
    return max(-bound, min(bound, bias))
