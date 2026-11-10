"""N9 - Demo data seeder.

Creates CLEARLY LABELLED synthetic invoice outcomes (is_synthetic = 1, paypal_invoice_id
'SYNTH-...') so the learning loop can be shown without waiting for real payments. They never
touch PayPal, are badged "synthetic" in the UI, and are deleted with one call. The generator has
a hidden bias - online-gap and pain-evidence predict payment more than review volume - so a
calibration run has something real to find, and the demo explains that openly.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone

from gtm_engine.paypal.calibration import DEFAULT_WEIGHTS
from gtm_engine.paypal.store import new_id

KEY_FOR = {"review_band": "review_band", "rating": "rating_score", "proximity_tier": "proximity_tier",
           "online_gap": "online_gap", "pain_evidence": "pain_evidence"}
# Hidden "true" effect on payment probability, as a share of each dimension's maximum.
TRUE_EFFECT = {"review_band": 0.05, "rating": 0.05, "proximity_tier": 0.10, "online_gap": 0.45, "pain_evidence": 0.40}


def generate(count: int = 60, seed: int = 7, now: datetime | None = None) -> list[dict]:
    rng = random.Random(seed)
    now = now or datetime.now(timezone.utc)
    rows = []
    for i in range(count):
        parts = {d: rng.randint(0, mx) for d, mx in DEFAULT_WEIGHTS.items()}
        ratio_sum = sum(TRUE_EFFECT[d] * parts[d] / DEFAULT_WEIGHTS[d] for d in parts)
        p = min(0.9, max(0.05, 0.08 + 0.7 * ratio_sum))
        paid = rng.random() < p
        sent = now - timedelta(days=rng.randint(16, 60))  # old enough to count as resolved
        status = "PAID" if paid else rng.choice(["SENT", "SENT", "CANCELLED"])
        breakdown = {KEY_FOR[d]: v for d, v in parts.items()}
        rows.append({
            "i": i, "score": sum(parts.values()), "breakdown": breakdown, "status": status,
            "sent_at": sent.isoformat(),
            "paid_at": (sent + timedelta(days=rng.randint(1, 10))).isoformat() if paid else None,
            "amount_cents": rng.choice([15000, 25000, 40000, 60000, 90000]),
        })
    return rows


def seed(db, *, campaign_id: str, owner_id: str | None, count: int = 60, seed_value: int = 7) -> int:
    count = max(1, min(count, 200))
    ts = datetime.now(timezone.utc).isoformat()
    for r in generate(count, seed_value):
        db._execute(
            "INSERT INTO invoices (invoice_id, lead_id, campaign_id, owner_id, proposal_id, paypal_invoice_id, "
            "invoice_number, idempotency_key, amount_cents, currency, status, payer_url, score_at_send, "
            "score_breakdown_at_send, is_synthetic, sent_at, paid_at, created_at, updated_at) "
            "VALUES (%s,%s,%s,%s,NULL,%s,%s,%s,%s,'USD',%s,NULL,%s,%s,1,%s,%s,%s,%s) "
            "ON CONFLICT (idempotency_key) DO NOTHING",
            (new_id("inv"), f"synthetic-lead-{campaign_id}-{r['i']}", campaign_id, owner_id,
             f"SYNTH-{campaign_id}-{r['i']}", f"SYN-{r['i']:04d}", f"synthetic:{campaign_id}:{seed_value}:{r['i']}",
             r["amount_cents"], r["status"], r["score"], json.dumps(r["breakdown"]),
             r["sent_at"], r["paid_at"], r["sent_at"], ts))
    db._commit()
    return count


def clear(db, *, campaign_id: str, owner_id: str | None) -> int:
    params: list = [campaign_id]
    q = "DELETE FROM invoices WHERE campaign_id = %s AND is_synthetic = 1"
    if owner_id is not None:
        q += " AND (owner_id = %s OR owner_id IS NULL)"
        params.append(owner_id)
    cur = db._execute(q, tuple(params))
    db._commit()
    return cur.rowcount
