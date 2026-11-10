"""N4 - Payment webhook handler: PayPal event -> invoice status.

Signature verification happens in the route (PayPalClient.verify_webhook_signature) *before*
anything here runs. This module is idempotent (each PayPal event id is processed once) and
monotonic (a late or replayed event can never move an invoice backwards).
"""

from __future__ import annotations

import json
import logging

from gtm_engine.paypal import store
from gtm_engine.paypal.client import PayPalClient
from gtm_engine.paypal.orders import captured_payment_matches, completed_capture, order_matches
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


def process_event(db, event: dict, raw_body: str, client: PayPalClient | None = None) -> dict:
    event_id = str(event.get("id") or "")
    etype = str(event.get("event_type") or "")
    if not event_id:
        return {"ok": False, "reason": "event has no id"}
    if etype.startswith(("CHECKOUT.ORDER.", "PAYMENT.CAPTURE.")):
        return _process_order_event(db, event, raw_body, event_id, etype, client)
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


def _process_order_event(db, event: dict, raw_body: str, event_id: str, etype: str,
                         client: PayPalClient | None) -> dict:
    resource = event.get("resource") or {}
    related = ((resource.get("supplementary_data") or {}).get("related_ids") or {})
    order_id = resource.get("id") if etype.startswith("CHECKOUT.ORDER.") else related.get("order_id")
    capture_id = resource.get("id") if etype.startswith("PAYMENT.CAPTURE.") else None
    order_id = order_id if isinstance(order_id, str) else None
    capture_id = capture_id if isinstance(capture_id, str) else None
    local = store.get_order_by_paypal_id(db, order_id) if order_id else None
    status = None

    if local and client and etype in {
        "CHECKOUT.ORDER.APPROVED", "CHECKOUT.ORDER.COMPLETED", "PAYMENT.CAPTURE.COMPLETED",
    }:
        remote = client.get_order(order_id)
        expected = {
            "order_id": order_id, "checkout_id": local["checkout_id"],
            "amount_cents": local["amount_cents"], "currency": local["currency"],
        }
        if etype == "CHECKOUT.ORDER.APPROVED":
            if order_matches(remote, **expected, status="APPROVED"):
                status = "APPROVED"
        else:
            capture = completed_capture(remote, **expected)
            if capture:
                verified_capture_id = capture["id"]
                if capture_id and capture_id != verified_capture_id:
                    status = "CAPTURE_PENDING"
                else:
                    capture_id = verified_capture_id
                    payment = client.get_capture(capture_id)
                    if captured_payment_matches(
                        payment, capture_id=capture_id, order_id=order_id,
                        amount_cents=local["amount_cents"], currency=local["currency"],
                    ):
                        status = "PAID"
                    else:
                        status = "CAPTURE_PENDING"
            else:
                status = "CAPTURE_PENDING"

    result = store.record_order_event(
        db, event_id=event_id, event_type=etype, paypal_order_id=order_id,
        paypal_capture_id=capture_id, raw=raw_body, status=status,
    )
    if not result.get("inserted"):
        return {"ok": True, "duplicate": True, "event_id": event_id}
    if not local:
        return {"ok": True, "unmatched": True, "event_type": etype}
    return {"ok": True, "checkout_id": local["checkout_id"],
            "status": result.get("status") or local["status"]}


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
