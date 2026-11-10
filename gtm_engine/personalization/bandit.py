"""Pure-Python per-user logistic contextual bandit (docs/PERSONALIZATION_PLAN.md).

Deterministic, no I/O, no numpy - the engine is otherwise pure-Python/psycopg and deploys on
Vercel/GitHub Actions, so this stays dependency-free. One online SGD step per feedback event:
`w <- w + lr * (reward - p) * x`."""

from __future__ import annotations

import math


def predict(weights: list[float], x: list[float]) -> float:
    """p = sigmoid(w . x) - "will this user act on this lead?", in [0, 1]."""
    z = sum(w * xi for w, xi in zip(weights, x))
    z = max(-60.0, min(60.0, z))  # avoid float overflow in exp() for pathological inputs
    return 1.0 / (1.0 + math.exp(-z))


def update(weights: list[float], x: list[float], reward: float, lr: float) -> list[float]:
    """One online SGD step. reward is 0.0 or 1.0 (skipped/dismissed vs approved/exported/contacted)."""
    p = predict(weights, x)
    error = reward - p
    return [w + lr * error * xi for w, xi in zip(weights, x)]
