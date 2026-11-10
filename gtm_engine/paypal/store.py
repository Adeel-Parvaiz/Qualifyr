"""N5 - Outcome ledger and the PayPal tables.

Plain SQL over the existing `Database` connection helpers (same thin-repository shape as
storage/database.py). Money is stored as integer cents.

Ledger integrity: an invoice row captures `score_at_send` and `score_breakdown_at_send` once,
at insert. No function in this module updates those columns, so later re-scoring or weight
changes cannot rewrite history. `payment_events` keeps every raw webhook payload for audit,
keyed by PayPal's unique event id.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

PAYPAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS proposals (
    proposal_id TEXT PRIMARY KEY,
    lead_id TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    owner_id TEXT,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    line_items TEXT NOT NULL,
    currency TEXT NOT NULL,
    amount_cents BIGINT NOT NULL,
    range_min_cents BIGINT NOT NULL,
    range_max_cents BIGINT NOT NULL,
    rationale TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'template',   -- llm | template
    status TEXT NOT NULL DEFAULT 'draft',      -- draft | approved | invoiced
    terms_hash TEXT,
    approved_by TEXT,
    approved_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_proposals_lead ON proposals(lead_id, created_at DESC);

CREATE TABLE IF NOT EXISTS invoices (
    invoice_id TEXT PRIMARY KEY,
    lead_id TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    owner_id TEXT,
    proposal_id TEXT,
    paypal_invoice_id TEXT UNIQUE,
    invoice_number TEXT,
    idempotency_key TEXT NOT NULL UNIQUE,
    amount_cents BIGINT NOT NULL,
    currency TEXT NOT NULL,
    status TEXT NOT NULL,                      -- PENDING | FAILED | DRAFT | SENT | PAID | CANCELLED | REFUNDED
    payer_url TEXT,
    score_at_send INTEGER NOT NULL,
    score_breakdown_at_send TEXT NOT NULL,     -- immutable snapshot (JSON)
    is_synthetic INTEGER NOT NULL DEFAULT 0,   -- 1 = clearly-labelled demo data
    sent_at TEXT,
    paid_at TEXT,
    cancelled_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_invoices_campaign ON invoices(campaign_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_invoices_lead ON invoices(lead_id);

CREATE TABLE IF NOT EXISTS payment_events (
    id SERIAL PRIMARY KEY,
    paypal_event_id TEXT NOT NULL UNIQUE,
    paypal_invoice_id TEXT,
    event_type TEXT NOT NULL,
    raw_payload TEXT NOT NULL,
    received_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_payment_events_invoice ON payment_events(paypal_invoice_id);

CREATE TABLE IF NOT EXISTS calibration_runs (
    run_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    owner_id TEXT,
    sample_size INTEGER NOT NULL,
    paid_count INTEGER NOT NULL,
    result_json TEXT NOT NULL,                 -- full analysis incl. current/proposed weights
    explanation TEXT NOT NULL,
    status TEXT NOT NULL,                      -- proposed | accepted | rejected | insufficient_data | no_change
    includes_synthetic INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    decided_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_calibration_campaign ON calibration_runs(campaign_id, created_at DESC);
"""

# Statuses that count as a resolved outcome for calibration.
UNPAID_AFTER_DAYS = 14


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _row(r):
    return dict(r) if r else None


def _scope(owner_id: str | None, params: list, col: str = "owner_id") -> str:
    """Tenant scope, mirroring the rest of the API: owner_id None (local operator/tests) sees
    everything; otherwise the caller's own rows plus legacy NULL-owner rows."""
    if owner_id is None:
        return ""
    params.append(owner_id)
    return f" AND ({col} = %s OR {col} IS NULL)"


# -- proposals ---------------------------------------------------------------

def insert_proposal(db, *, lead_id, campaign_id, owner_id, draft) -> dict:
    pid, ts = new_id("prop"), now_iso()
    db._execute(
        "INSERT INTO proposals (proposal_id, lead_id, campaign_id, owner_id, subject, body, line_items, "
        "currency, amount_cents, range_min_cents, range_max_cents, rationale, source, status, created_at, updated_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'draft',%s,%s)",
        (pid, lead_id, campaign_id, owner_id, draft.subject, draft.body, json.dumps(draft.line_items),
         draft.currency, draft.amount_cents, draft.range_min_cents, draft.range_max_cents,
         draft.rationale, draft.source, ts, ts))
    db._commit()
    return get_proposal(db, pid)


def _decode_proposal(r: dict | None) -> dict | None:
    if not r:
        return None
    r["line_items"] = json.loads(r["line_items"])
    return r


def get_proposal(db, proposal_id: str) -> dict | None:
    return _decode_proposal(_row(db._execute(
        "SELECT * FROM proposals WHERE proposal_id = %s", (proposal_id,)).fetchone()))


def latest_proposal(db, lead_id: str) -> dict | None:
    return _decode_proposal(_row(db._execute(
        "SELECT * FROM proposals WHERE lead_id = %s ORDER BY created_at DESC LIMIT 1", (lead_id,)).fetchone()))


def update_proposal_terms(db, proposal_id: str, *, subject, body, line_items, currency, amount_cents) -> dict:
    """Any edit puts the proposal back to 'draft' and clears the approval: approval always refers
    to the exact terms that were on screen when the human clicked it."""
    db._execute(
        "UPDATE proposals SET subject=%s, body=%s, line_items=%s, currency=%s, amount_cents=%s, "
        "status='draft', terms_hash=NULL, approved_by=NULL, approved_at=NULL, updated_at=%s "
        "WHERE proposal_id=%s AND status <> 'invoiced'",
        (subject, body, json.dumps(line_items), currency, amount_cents, now_iso(), proposal_id))
    db._commit()
    return get_proposal(db, proposal_id)


def approve_proposal(db, proposal_id: str, approved_by: str, terms_hash: str) -> dict:
    db._execute(
        "UPDATE proposals SET status='approved', approved_by=%s, approved_at=%s, terms_hash=%s, updated_at=%s "
        "WHERE proposal_id=%s AND status <> 'invoiced'",
        (approved_by, now_iso(), terms_hash, now_iso(), proposal_id))
    db._commit()
    return get_proposal(db, proposal_id)


def mark_proposal_invoiced(db, proposal_id: str) -> None:
    db._execute("UPDATE proposals SET status='invoiced', updated_at=%s WHERE proposal_id=%s",
                (now_iso(), proposal_id))
    db._commit()


# -- invoices (the outcome ledger) ----------------------------------------------

def _decode_invoice(r: dict | None) -> dict | None:
    if not r:
        return None
    try:
        r["score_breakdown_at_send"] = json.loads(r["score_breakdown_at_send"])
    except (TypeError, ValueError):
        r["score_breakdown_at_send"] = {}
    return r


def claim_invoice(db, *, idempotency_key, lead_id, campaign_id, owner_id, proposal_id, amount_cents,
                  currency, score_at_send, score_breakdown, is_synthetic=False) -> tuple[dict, bool]:
    """Reserve the one invoice allowed per (lead, proposal). Returns (row, created).

    The UNIQUE idempotency_key makes a double click or retry resolve to the same row, so two
    invoices can never be created for one approval. A previous FAILED attempt may be retried."""
    iid, ts = new_id("inv"), now_iso()
    cur = db._execute(
        "INSERT INTO invoices (invoice_id, lead_id, campaign_id, owner_id, proposal_id, idempotency_key, "
        "amount_cents, currency, status, score_at_send, score_breakdown_at_send, is_synthetic, created_at, updated_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'PENDING',%s,%s,%s,%s,%s) "
        "ON CONFLICT (idempotency_key) DO NOTHING RETURNING invoice_id",
        (iid, lead_id, campaign_id, owner_id, proposal_id, idempotency_key, amount_cents, currency,
         int(score_at_send), json.dumps(score_breakdown), int(is_synthetic), ts, ts))
    created = cur.fetchone() is not None
    if not created:
        cur2 = db._execute("UPDATE invoices SET status='PENDING', updated_at=%s "
                           "WHERE idempotency_key=%s AND status='FAILED' RETURNING invoice_id",
                           (ts, idempotency_key))
        created = cur2.fetchone() is not None
    db._commit()
    row = _decode_invoice(_row(db._execute(
        "SELECT * FROM invoices WHERE idempotency_key = %s", (idempotency_key,)).fetchone()))
    return row, created


def finish_invoice(db, invoice_id: str, *, paypal_invoice_id, invoice_number, status, payer_url, sent_at) -> dict:
    db._execute(
        "UPDATE invoices SET paypal_invoice_id=%s, invoice_number=%s, status=%s, payer_url=%s, sent_at=%s, "
        "updated_at=%s WHERE invoice_id=%s",
        (paypal_invoice_id, invoice_number, status, payer_url, sent_at, now_iso(), invoice_id))
    db._commit()
    return get_invoice(db, invoice_id)


def fail_invoice(db, invoice_id: str) -> None:
    db._execute("UPDATE invoices SET status='FAILED', updated_at=%s WHERE invoice_id=%s AND status='PENDING'",
                (now_iso(), invoice_id))
    db._commit()


def get_invoice(db, invoice_id: str) -> dict | None:
    return _decode_invoice(_row(db._execute("SELECT * FROM invoices WHERE invoice_id = %s", (invoice_id,)).fetchone()))


def get_invoice_by_paypal_id(db, paypal_invoice_id: str) -> dict | None:
    return _decode_invoice(_row(db._execute(
        "SELECT * FROM invoices WHERE paypal_invoice_id = %s", (paypal_invoice_id,)).fetchone()))


def latest_invoice_for_lead(db, lead_id: str) -> dict | None:
    return _decode_invoice(_row(db._execute(
        "SELECT * FROM invoices WHERE lead_id = %s AND status <> 'FAILED' ORDER BY created_at DESC LIMIT 1",
        (lead_id,)).fetchone()))


def set_invoice_status(db, invoice_id: str, status: str, when: str) -> None:
    """Status-only update. Deliberately cannot touch the score snapshot columns."""
    col = {"PAID": ", paid_at = %s", "CANCELLED": ", cancelled_at = %s", "SENT": ", sent_at = COALESCE(sent_at, %s)"}.get(status, "")
    params: list = [status, now_iso()]
    if col:
        params.append(when)
    params.append(invoice_id)
    db._execute(f"UPDATE invoices SET status = %s, updated_at = %s{col} WHERE invoice_id = %s", tuple(params))
    db._commit()


def list_invoices(db, owner_id: str | None, campaign_id: str | None = None, limit: int = 200) -> list[dict]:
    params: list = []
    where = "WHERE status <> 'FAILED'" + _scope(owner_id, params)
    if campaign_id:
        where += " AND campaign_id = %s"
        params.append(campaign_id)
    params.append(max(1, min(limit, 500)))
    rows = db._execute(f"SELECT * FROM invoices {where} ORDER BY created_at DESC LIMIT %s", tuple(params)).fetchall()
    return [_decode_invoice(dict(r)) for r in rows]


# -- webhook events ---------------------------------------------------------------

def record_event(db, event_id: str, event_type: str, paypal_invoice_id: str | None, raw: str) -> bool:
    """True if this event id is new. ON CONFLICT makes concurrent duplicate deliveries safe."""
    cur = db._execute(
        "INSERT INTO payment_events (paypal_event_id, paypal_invoice_id, event_type, raw_payload, received_at) "
        "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (paypal_event_id) DO NOTHING RETURNING id",
        (event_id, paypal_invoice_id, event_type, raw, now_iso()))
    inserted = cur.fetchone() is not None
    db._commit()
    return inserted


def events_for_invoice(db, paypal_invoice_id: str) -> list[dict]:
    rows = db._execute(
        "SELECT paypal_event_id, event_type, received_at FROM payment_events WHERE paypal_invoice_id = %s ORDER BY id",
        (paypal_invoice_id,)).fetchall()
    return [dict(r) for r in rows]


# -- revenue + calibration data ---------------------------------------------------

def revenue_summary(db, owner_id: str | None, campaign_id: str | None = None) -> dict:
    params: list = []
    where = "WHERE status <> 'FAILED'" + _scope(owner_id, params)
    if campaign_id:
        where += " AND campaign_id = %s"
        params.append(campaign_id)
    rows = db._execute(
        f"SELECT campaign_id, currency, status, is_synthetic, COUNT(*) AS n, COALESCE(SUM(amount_cents),0) AS cents "
        f"FROM invoices {where} GROUP BY campaign_id, currency, status, is_synthetic", tuple(params)).fetchall()
    by_status: dict[str, int] = {}
    paid_cents: dict[str, int] = {}
    per_campaign: dict[str, dict] = {}
    synthetic = 0
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + int(r["n"])
        if r["is_synthetic"]:
            synthetic += int(r["n"])
        c = per_campaign.setdefault(r["campaign_id"], {"campaign_id": r["campaign_id"], "invoices": 0, "paid": 0, "paid_cents": 0})
        c["invoices"] += int(r["n"])
        if r["status"] == "PAID":
            c["paid"] += int(r["n"])
            c["paid_cents"] += int(r["cents"])
            paid_cents[r["currency"]] = paid_cents.get(r["currency"], 0) + int(r["cents"])
    total = sum(by_status.values())
    paid = by_status.get("PAID", 0)
    return {
        "total_invoices": total, "paid_invoices": paid,
        "paid_rate": round(paid / total, 4) if total else 0.0,
        "paid_cents_by_currency": paid_cents, "by_status": by_status,
        "per_campaign": list(per_campaign.values()), "synthetic_count": synthetic,
    }


def calibration_outcomes(db, owner_id: str | None, campaign_id: str, include_synthetic: bool = True) -> list[dict]:
    """Resolved outcomes only: PAID = 1; CANCELLED, or SENT for longer than UNPAID_AFTER_DAYS = 0.
    Recently sent invoices are still pending and excluded so they cannot bias the rate downward."""
    params: list = [campaign_id]
    where = "WHERE campaign_id = %s AND status IN ('PAID','CANCELLED','SENT','REFUNDED')" + _scope(owner_id, params)
    if not include_synthetic:
        where += " AND is_synthetic = 0"
    rows = db._execute(f"SELECT * FROM invoices {where}", tuple(params)).fetchall()
    cutoff = datetime.now(timezone.utc).timestamp() - UNPAID_AFTER_DAYS * 86400
    out = []
    for r in rows:
        r = _decode_invoice(dict(r))
        if r["status"] in ("PAID", "REFUNDED"):
            paid = r["status"] == "PAID"  # a refunded invoice did not stay revenue
        elif r["status"] == "CANCELLED":
            paid = False
        else:
            try:
                sent = datetime.fromisoformat((r["sent_at"] or r["created_at"]).replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
            if sent > cutoff:
                continue  # still pending
            paid = False
        out.append({"paid": paid, "total": r["score_at_send"], "breakdown": r["score_breakdown_at_send"],
                    "is_synthetic": bool(r["is_synthetic"])})
    return out


def save_calibration_run(db, *, campaign_id, owner_id, result: dict, includes_synthetic: bool) -> dict:
    rid = new_id("cal")
    db._execute(
        "INSERT INTO calibration_runs (run_id, campaign_id, owner_id, sample_size, paid_count, result_json, "
        "explanation, status, includes_synthetic, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (rid, campaign_id, owner_id, result["sample_size"], result["paid_count"], json.dumps(result),
         result["explanation"], result["status"], int(includes_synthetic), now_iso()))
    db._commit()
    return get_calibration_run(db, rid)


def get_calibration_run(db, run_id: str) -> dict | None:
    r = _row(db._execute("SELECT * FROM calibration_runs WHERE run_id = %s", (run_id,)).fetchone())
    if r:
        r["result"] = json.loads(r.pop("result_json"))
    return r


def list_calibration_runs(db, owner_id: str | None, campaign_id: str, limit: int = 10) -> list[dict]:
    params: list = [campaign_id]
    where = "WHERE campaign_id = %s" + _scope(owner_id, params)
    params.append(limit)
    rows = db._execute(f"SELECT * FROM calibration_runs {where} ORDER BY created_at DESC LIMIT %s", tuple(params)).fetchall()
    out = []
    for r in rows:
        r = dict(r)
        r["result"] = json.loads(r.pop("result_json"))
        out.append(r)
    return out


def decide_calibration_run(db, run_id: str, status: str) -> dict | None:
    db._execute("UPDATE calibration_runs SET status=%s, decided_at=%s WHERE run_id=%s AND status='proposed'",
                (status, now_iso(), run_id))
    db._commit()
    return get_calibration_run(db, run_id)
