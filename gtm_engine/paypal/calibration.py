"""N6 - Score calibration engine. Pure, stdlib-only, fully deterministic.

Learns which score dimensions predict *payment* and proposes new weights. Design rules:

  * Ground truth is PayPal: an outcome is "paid" only if PayPal reported the invoice PAID.
  * Smoothed rates: each bucket's paid rate is shrunk toward the overall rate
    ((paid + k*p0) / (n + k)), so a 3-lead bucket cannot swing the result.
  * Minimum sample: no proposal at all below MIN_TOTAL_OUTCOMES, and a dimension is only
    adjusted when both its high and low buckets hold at least MIN_BUCKET outcomes.
  * Bounded: each weight moves at most MAX_STEP_POINTS per cycle, and the weights always still
    sum to the same total (100) so the 0-100 score keeps its meaning.
  * Never silent: this module only *proposes*. A human accepts or rejects (see the API).
  * History is safe: outcomes use the score snapshot taken when the invoice was sent, so
    re-scoring later cannot rewrite what the learner sees.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Weight key -> key inside the stored ScoreBreakdown snapshot.
DIMENSIONS: dict[str, str] = {
    "review_band": "review_band",
    "rating": "rating_score",
    "proximity_tier": "proximity_tier",
    "online_gap": "online_gap",
    "pain_evidence": "pain_evidence",
}
LABELS = {
    "review_band": "review volume",
    "rating": "Google rating",
    "proximity_tier": "proximity",
    "online_gap": "online-presence gap",
    "pain_evidence": "pain evidence",
}
DEFAULT_WEIGHTS = {"review_band": 30, "rating": 10, "proximity_tier": 15, "online_gap": 25, "pain_evidence": 20}

SMOOTHING_K = 5.0
MIN_TOTAL_OUTCOMES = 20
MIN_BUCKET = 5
MAX_STEP_POINTS = 5
LIFT_GAIN = 0.5
MULT_MIN, MULT_MAX = 0.8, 1.2
HIGH_RATIO = 0.5  # a dimension counts as "strong" when it earned >= half its max points
SCORE_BANDS = ((0, 39), (40, 54), (55, 69), (70, 100))


@dataclass(frozen=True)
class Outcome:
    paid: bool
    total: int
    breakdown: dict  # ScoreBreakdown-shaped snapshot taken at send time


def smoothed_rate(paid: int, n: int, prior: float, k: float = SMOOTHING_K) -> float:
    return (paid + k * prior) / (n + k)


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _integerize(floats: dict[str, float], lows: dict[str, int], highs: dict[str, int],
                target: int) -> dict[str, int] | None:
    """Round to integers that sum to `target`, each within [low, high]. Largest-remainder.
    Returns None if the constraints cannot be met."""
    out = {k: int(_clamp(math.floor(v), lows[k], highs[k])) for k, v in floats.items()}
    diff = target - sum(out.values())
    order = sorted(floats, key=lambda k: floats[k] - math.floor(floats[k]), reverse=(diff > 0))
    guard = 0
    while diff != 0 and guard < 1000:
        moved = False
        for k in order:
            if diff > 0 and out[k] < highs[k]:
                out[k] += 1; diff -= 1; moved = True
            elif diff < 0 and out[k] > lows[k]:
                out[k] -= 1; diff += 1; moved = True
            if diff == 0:
                break
        if not moved:
            return None
        guard += 1
    return out if diff == 0 else None


def score_band_rates(outcomes: list[Outcome]) -> list[dict]:
    """Paid rate per total-score band, for the Score-vs-Paid chart."""
    rows = []
    for lo, hi in SCORE_BANDS:
        grp = [o for o in outcomes if lo <= o.total <= hi]
        paid = sum(1 for o in grp if o.paid)
        rows.append({"band": f"{lo}-{hi}", "n": len(grp), "paid": paid,
                     "rate": round(paid / len(grp), 4) if grp else None})
    return rows


def analyse(outcomes: list[Outcome], weights: dict[str, int] | None = None) -> dict:
    """Return a JSON-able calibration result (never mutates anything)."""
    current = {k: int((weights or DEFAULT_WEIGHTS).get(k, DEFAULT_WEIGHTS[k])) for k in DIMENSIONS}
    n = len(outcomes)
    paid_n = sum(1 for o in outcomes if o.paid)
    base = {
        "sample_size": n, "paid_count": paid_n,
        "base_rate": round(paid_n / n, 4) if n else 0.0,
        "current_weights": current, "proposed_weights": dict(current),
        "dimension_stats": [], "changes": [], "score_bands": score_band_rates(outcomes),
        "thresholds": {"min_total": MIN_TOTAL_OUTCOMES, "min_bucket": MIN_BUCKET,
                       "max_step_points": MAX_STEP_POINTS},
    }
    if n < MIN_TOTAL_OUTCOMES:
        base["status"] = "insufficient_data"
        base["explanation"] = (f"Not enough resolved invoices to learn from yet: {n} so far, "
                               f"{MIN_TOTAL_OUTCOMES} needed. Weights are unchanged.")
        return base
    p0 = paid_n / n
    if p0 in (0.0, 1.0):
        base["status"] = "no_change"
        base["explanation"] = ("Every resolved invoice had the same outcome, so no dimension can be "
                               "told apart from another. Weights are unchanged.")
        return base

    stats: list[dict] = []
    mult: dict[str, float] = {}
    for dim, key in DIMENSIONS.items():
        w = current[dim]
        hi_n = hi_p = lo_n = lo_p = 0
        for o in outcomes:
            pts = float((o.breakdown or {}).get(key, 0) or 0)
            strong = w > 0 and (pts / w) >= HIGH_RATIO
            if strong:
                hi_n += 1; hi_p += int(o.paid)
            else:
                lo_n += 1; lo_p += int(o.paid)
        s_hi, s_lo = smoothed_rate(hi_p, hi_n, p0), smoothed_rate(lo_p, lo_n, p0)
        eligible = hi_n >= MIN_BUCKET and lo_n >= MIN_BUCKET
        ratio = s_hi / s_lo if s_lo > 0 else 1.0
        m = _clamp(1 + LIFT_GAIN * (ratio - 1), MULT_MIN, MULT_MAX) if eligible else 1.0
        mult[dim] = m
        stats.append({
            "dimension": dim, "label": LABELS[dim], "weight": w,
            "n_high": hi_n, "paid_high": hi_p, "n_low": lo_n, "paid_low": lo_p,
            "rate_high": round(s_hi, 4), "rate_low": round(s_lo, 4),
            "lift_vs_average": round(s_hi / p0, 3), "multiplier": round(m, 3), "eligible": eligible,
        })
    base["dimension_stats"] = stats

    target = sum(current.values())
    raw = {d: current[d] * mult[d] for d in DIMENSIONS}
    total_raw = sum(raw.values()) or 1.0
    scaled = {d: raw[d] * target / total_raw for d in DIMENSIONS}
    lows = {d: max(1 if current[d] > 0 else 0, current[d] - MAX_STEP_POINTS) for d in DIMENSIONS}
    highs = {d: current[d] + MAX_STEP_POINTS for d in DIMENSIONS}
    scaled = {d: _clamp(scaled[d], lows[d], highs[d]) for d in DIMENSIONS}
    proposed = _integerize(scaled, lows, highs, target)
    if proposed is None or proposed == current:
        base["status"] = "no_change"
        base["explanation"] = (f"Across {n} resolved invoices ({paid_n} paid, {p0:.0%}), no dimension "
                               "separated paying leads from non-paying ones strongly enough to justify "
                               "a change. Weights are unchanged.")
        return base

    changes, sentences = [], []
    by_dim = {s["dimension"]: s for s in stats}
    for d in DIMENSIONS:
        if proposed[d] == current[d]:
            continue
        s = by_dim[d]
        up = proposed[d] > current[d]
        lift = s["lift_vs_average"]
        sentences.append(
            f"Leads with strong {s['label']} ({s['n_high']} of {n}) paid "
            f"{lift:.1f}x the average rate, so its weight {'rises' if up else 'falls'} "
            f"from {current[d]} to {proposed[d]}." if abs(lift - 1) >= 0.05 else
            f"{s['label'].capitalize()} was rebalanced from {current[d]} to {proposed[d]} to keep the total at {target}."
        )
        changes.append({"dimension": d, "label": s["label"], "from": current[d], "to": proposed[d]})
    base["proposed_weights"] = proposed
    base["changes"] = changes
    base["status"] = "proposed"
    base["explanation"] = (
        f"Based on {n} resolved PayPal invoices ({paid_n} paid, {p0:.0%} overall). "
        + " ".join(sentences)
        + f" Each weight moves at most {MAX_STEP_POINTS} points per cycle and the total stays {target}. "
        "Nothing changes until you accept."
    )
    return base
