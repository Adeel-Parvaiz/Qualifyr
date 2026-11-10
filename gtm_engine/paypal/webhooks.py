"""N4 - Payment webhook handler: PayPal event -> invoice status.

Signature verification happens in the route (PayPalClient.verify_webhook_signature) *before*
anything here runs. This module is idempotent (each PayPal event id is processed once) and
monotonic (a late or replayed event can never move an invoice backwards).
"""

from __future__ import annotations

import json
import logging

from gtm_engine.paypal import store
from gtm_engine.paypal.store import now_iso

log = logging.getLogger(__name__)

# PayPal Invoicing webhook event types we act on. Any other type is still stored for audit.
EVENT_STATUS = {
    "INVOICING.INVOICE.CREATED": "DRAFT",
    "INVOICING.INVOICE.SENT": "SENT",
    "INVOICING.INVOICE.PAID": "PAID",
    "INVOICING.INVOICE.CANCELLED": "CANCELLED",
    "INVOICING.INVOICE.REFUNDED": "REFUNDED",
}
# Higher rank wins. CANCELLED cannot override PAID; REFUNDED supersedes PAID.
RANK = {"PENDING": 0, "FAILED": 0, "DRAFT": 1, "SENT": 2, "CANCELLED": 3, "PAID": 4, "REFUNDED": 5}


def invoice_id_of(event: dict) -> str | None:
    """PayPal nests the invoice either directly in `resource` or under `resource.invoice`
    depending on the event version; accept both."""
    res = event.get("resource") or {}
    cands = [res.get("id"), (res.get("invoice") or {}).get("id")]
    cands = [c for c in cands if isinstance(c, str) and c]
    for c in cands:
        if c.upper().startswith("INV"):
            return c
    return cands[0] if cands else None


def process_event(db, event: dict, raw_body: str) -> dict:
    event_id = str(event.get("id") or "")
    etype = str(event.get("event_type") or "")
    if not event_id:
        return {"ok": False, "reason": "event has no id"}
    pp_invoice = invoice_id_of(event)
    inserted = store.record_event(db, event_id, etype, pp_invoice, raw_body)
    if not inserted:
        return {"ok": True, "duplicate": True, "event_id": event_id}

    new_status = EVENT_STATUS.get(etype)
    if not new_status or not pp_invoice:
        return {"ok": True, "ignored": True, "event_type": etype}
    inv = store.get_invoice_by_paypal_id(db, pp_invoice)
    if inv is None:
        # An invoice from another app on the same sandbox account. Stored for audit, not ours.
        return {"ok": True, "unmatched": True, "paypal_invoice_id": pp_invoice}
    if RANK.get(new_status, 0) <= RANK.get(inv["status"], 0):
        return {"ok": True, "stale": True, "status": inv["status"]}

    when = str(event.get("create_time") or now_iso())
    store.set_invoice_status(db, inv["invoice_id"], new_status, when)
    try:
        db.add_event(inv["lead_id"], f"invoice_{new_status.lower()}", detail=pp_invoice)
    except Exception as exc:  # noqa: BLE001 - the ledger update above is what matters
        log.debug("outreach event log failed: %s", exc)
    return {"ok": True, "invoice_id": inv["invoice_id"], "status": new_status}


def sync_from_details(db, inv: dict, details: dict) -> dict:
    """Pull-based fallback (webhooks can be delayed or misconfigured): reconcile one invoice
    from PayPal's own `GET invoice` response using the same monotonic rules."""
    pp_status = str(details.get("status") or "").upper()
    mapped = {"DRAFT": "DRAFT", "SENT": "SENT", "UNPAID": "SENT", "PAYMENT_PENDING": "SENT",
              "PAID": "PAID", "MARKED_AS_PAID": "PAID", "CANCELLED": "CANCELLED",
              "REFUNDED": "REFUNDED", "PARTIALLY_REFUNDED": "PAID", "MARKED_AS_REFUNDED": "REFUNDED"}.get(pp_status)
    if mapped and RANK.get(mapped, 0) > RANK.get(inv["status"], 0):
        store.set_invoice_status(db, inv["invoice_id"], mapped, now_iso())
        return {"changed": True, "status": mapped}
    return {"changed": False, "status": inv["status"], "paypal_status": pp_status}
