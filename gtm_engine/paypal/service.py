"""N2 - Invoice builder service: approved proposal -> PayPal invoice -> payer link in the email draft.

The approval gate lives HERE (not only in the HTTP route) so every caller - the API, the agent
tools, a future CLI - gets the same guarantee: no invoice exists without a human approval record
whose fingerprint still matches the proposal's current terms.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from gtm_engine.paypal import store
from gtm_engine.paypal.calibration import DIMENSIONS
from gtm_engine.paypal.client import PayPalClient, PayPalError
from gtm_engine.paypal.config import PayPalConfig
from gtm_engine.paypal.invoices import (
    InvoiceError, build_invoice_payload, new_invoice_number, normalize_line_items, payer_link,
    terms_hash, validate_currency,
)
from gtm_engine.paypal.orders import (
    OrderError, approval_url, build_order_payload, captured_payment_matches,
    completed_capture, order_matches,
)


class ApprovalRequired(Exception):
    """Raised when there is no valid human approval for the proposal (maps to HTTP 409)."""


class InvoiceInProgress(Exception):
    pass


def _checkout_view(order: dict) -> dict:
    return {
        "checkout_id": order["checkout_id"],
        "status": order["status"],
        "approval_url": order.get("approval_url"),
        "amount_cents": order["amount_cents"],
        "currency": order["currency"],
        "created_at": order["created_at"],
        "updated_at": order["updated_at"],
    }


def public_checkout_status(order: dict) -> dict:
    return {
        "status": order["status"],
        "paid": order["status"] == "PAID",
        "updated_at": order["updated_at"],
    }


def _checkout_capability(cfg: PayPalConfig, checkout_id: str) -> str:
    digest = hmac.new(
        cfg.client_secret.encode(), f"paypal-checkout:{checkout_id}".encode(), hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _return_url(base: str, checkout_id: str, capability: str, *, cancelled: bool = False) -> str:
    parts = urlsplit(base)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update({"checkout_id": checkout_id, "capability": capability})
    if cancelled:
        query["cancelled"] = "1"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _approved_terms(db, proposal_id: str, lead_id: str, cfg: PayPalConfig) -> tuple[dict, list[dict], int, str]:
    prop = store.get_proposal(db, proposal_id)
    if not prop or prop["lead_id"] != lead_id:
        raise ApprovalRequired("no such proposal for this lead")
    if prop["status"] != "approved" or not prop["approved_by"]:
        raise ApprovalRequired("the proposal must be approved by a person before checkout")
    items, total = normalize_line_items(prop["line_items"], max_total_cents=cfg.max_invoice_cents)
    currency = validate_currency(prop["currency"], cfg.currencies)
    if total != prop["amount_cents"] or terms_hash(prop["subject"], prop["body"], items, currency) != prop["terms_hash"]:
        raise ApprovalRequired("the proposal terms changed after approval; approve it again")
    return prop, items, total, currency


def create_order(db, client: PayPalClient, cfg: PayPalConfig, *, lead, proposal_id: str,
                 owner_id: str | None) -> dict:
    if not cfg.checkout_return_url or not cfg.checkout_cancel_url:
        raise PayPalError("PayPal Orders checkout return and cancel URLs are not configured", status=503)
    if not cfg.webhook_configured:
        raise PayPalError("PayPal Orders checkout requires a configured webhook", status=503)

    prop, items, total, currency = _approved_terms(db, proposal_id, lead.lead_id, cfg)
    order, created = store.claim_order(
        db, lead_id=lead.lead_id, campaign_id=lead.campaign_id, owner_id=owner_id,
        proposal_id=proposal_id, amount_cents=total, currency=currency,
        create_request_id=f"c-{uuid.uuid4().hex}", capture_request_id=f"p-{uuid.uuid4().hex}",
    )
    if not created:
        if order["status"] == "FAILED" and not order.get("paypal_order_id"):
            order = store.retry_order_creation(db, order["checkout_id"]) or order
        if order["status"] in {"FAILED", "PAID", "CAPTURING", "CAPTURE_PENDING", "APPROVED"}:
            return _checkout_view(order)
        if order.get("approval_url"):
            return _checkout_view(order)
        if order.get("paypal_order_id"):
            try:
                details = client.get_order(order["paypal_order_id"])
                if not order_matches(details, order_id=order["paypal_order_id"],
                                     checkout_id=order["checkout_id"], amount_cents=total,
                                     currency=currency):
                    return _checkout_view(order)
                recovered_url = approval_url(details)
            except (PayPalError, OrderError):
                return _checkout_view(order)
            order = store.finish_order_creation(
                db, order["checkout_id"], paypal_order_id=order["paypal_order_id"],
                approval_url=recovered_url,
            ) or order
            return _checkout_view(order)
        if order["status"] == "CREATING":
            try:
                attempted_at = datetime.fromisoformat(order["updated_at"])
                if attempted_at.tzinfo is None:
                    attempted_at = attempted_at.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                return _checkout_view(order)
            if datetime.now(timezone.utc) - attempted_at > timedelta(hours=5):
                return _checkout_view(order)

    payload = build_order_payload(
        checkout_id=order["checkout_id"], subject=prop["subject"], line_items=items,
        amount_cents=total, currency=currency,
        return_url=_return_url(cfg.checkout_return_url, order["checkout_id"],
                               _checkout_capability(cfg, order["checkout_id"])),
        cancel_url=_return_url(cfg.checkout_cancel_url, order["checkout_id"],
                               _checkout_capability(cfg, order["checkout_id"]),
                               cancelled=True),
    )
    try:
        remote = client.create_order(payload, request_id=order["create_request_id"])
    except PayPalError as exc:
        if not exc.outcome_unknown:
            store.mark_order_failed(db, order["checkout_id"])
            raise
        return _checkout_view(store.get_order(db, order["checkout_id"]) or order)

    paypal_order_id = remote.get("id")
    if not isinstance(paypal_order_id, str) or not paypal_order_id:
        return _checkout_view(order)
    order = store.finish_order_creation(
        db, order["checkout_id"], paypal_order_id=paypal_order_id, approval_url=None,
    ) or order
    try:
        if not order_matches(remote, order_id=paypal_order_id, checkout_id=order["checkout_id"],
                             amount_cents=total, currency=currency):
            return _checkout_view(order)
        href = approval_url(remote)
    except OrderError:
        return _checkout_view(order)
    order = store.finish_order_creation(
        db, order["checkout_id"], paypal_order_id=paypal_order_id, approval_url=href,
    ) or order
    return _checkout_view(order)


def _confirmed_capture(db, client: PayPalClient, order: dict, remote: dict) -> dict:
    checkout_id = order["checkout_id"]
    expected = {
        "order_id": order["paypal_order_id"],
        "checkout_id": checkout_id,
        "amount_cents": order["amount_cents"],
        "currency": order["currency"],
    }
    capture = completed_capture(remote, **expected)
    if capture is None:
        return _checkout_view(store.update_order_status(db, checkout_id, "CAPTURE_PENDING") or order)
    capture_id = capture["id"]
    try:
        details = client.get_capture(capture_id)
    except PayPalError:
        return _checkout_view(store.update_order_status(
            db, checkout_id, "CAPTURE_PENDING", capture_id=capture_id) or order)
    if not captured_payment_matches(
        details, capture_id=capture_id, order_id=order["paypal_order_id"],
        amount_cents=order["amount_cents"], currency=order["currency"],
    ):
        return _checkout_view(store.update_order_status(
            db, checkout_id, "CAPTURE_PENDING", capture_id=capture_id) or order)
    paid_at = str(details.get("create_time") or store.now_iso())
    return _checkout_view(store.update_order_status(
        db, checkout_id, "PAID", capture_id=capture_id, paid_at=paid_at) or order)


def return_from_checkout(db, client: PayPalClient, cfg: PayPalConfig, *, checkout_id: str,
                         capability: str, paypal_order_id: str) -> dict:
    order = store.get_order(db, checkout_id)
    if not order or not hmac.compare_digest(_checkout_capability(cfg, checkout_id), capability):
        raise ApprovalRequired("checkout not found")
    if not order.get("paypal_order_id") or not hmac.compare_digest(
        order["paypal_order_id"], paypal_order_id
    ):
        raise ApprovalRequired("checkout not found")
    if order["status"] == "PAID":
        return public_checkout_status(order)
    prop, _, total, currency = _approved_terms(db, order["proposal_id"], order["lead_id"], cfg)
    if total != order["amount_cents"] or currency != order["currency"] or not prop:
        raise ApprovalRequired("approved checkout terms no longer match")

    try:
        remote = client.get_order(order["paypal_order_id"])
    except PayPalError:
        return public_checkout_status(order)
    if not order_matches(remote, order_id=order["paypal_order_id"], checkout_id=checkout_id,
                         amount_cents=total, currency=currency):
        return public_checkout_status(order)
    if remote.get("status") == "COMPLETED":
        result = _confirmed_capture(db, client, order, remote)
        return {"status": result["status"], "paid": result["status"] == "PAID",
                "updated_at": result["updated_at"]}
    if remote.get("status") != "APPROVED":
        return public_checkout_status(order)

    store.update_order_status(db, checkout_id, "APPROVED")
    store.update_order_status(db, checkout_id, "CAPTURING")
    try:
        client.capture_order(order["paypal_order_id"], request_id=order["capture_request_id"])
    except PayPalError as exc:
        if not exc.outcome_unknown and exc.provider_status not in (408, 409):
            try:
                after_error = client.get_order(order["paypal_order_id"])
            except PayPalError:
                return public_checkout_status(
                    store.update_order_status(db, checkout_id, "CAPTURE_PENDING") or order)
            if order_matches(after_error, order_id=order["paypal_order_id"], checkout_id=checkout_id,
                             amount_cents=total, currency=currency, status="COMPLETED"):
                result = _confirmed_capture(db, client, order, after_error)
                return {"status": result["status"], "paid": result["status"] == "PAID",
                        "updated_at": result["updated_at"]}
            store.mark_order_failed(db, checkout_id)
            return public_checkout_status(store.get_order(db, checkout_id) or order)
        return public_checkout_status(store.update_order_status(db, checkout_id, "CAPTURE_PENDING") or order)

    try:
        after_capture = client.get_order(order["paypal_order_id"])
    except PayPalError:
        return public_checkout_status(store.update_order_status(db, checkout_id, "CAPTURE_PENDING") or order)
    if not order_matches(after_capture, order_id=order["paypal_order_id"], checkout_id=checkout_id,
                         amount_cents=total, currency=currency, status="COMPLETED"):
        return public_checkout_status(store.update_order_status(db, checkout_id, "CAPTURE_PENDING") or order)
    result = _confirmed_capture(db, client, order, after_capture)
    return {"status": result["status"], "paid": result["status"] == "PAID",
            "updated_at": result["updated_at"]}


def get_public_checkout_status(db, cfg: PayPalConfig, checkout_id: str, capability: str) -> dict:
    order = store.get_order(db, checkout_id)
    if not order or not hmac.compare_digest(_checkout_capability(cfg, checkout_id), capability):
        raise ApprovalRequired("checkout not found")
    return public_checkout_status(order)


def refresh_order(db, client: PayPalClient, cfg: PayPalConfig, checkout_id: str) -> dict:
    order = store.get_order(db, checkout_id)
    if not order:
        raise ApprovalRequired("checkout not found")
    if order["status"] == "PAID" or not order.get("paypal_order_id"):
        return _checkout_view(order)
    try:
        remote = client.get_order(order["paypal_order_id"])
    except PayPalError:
        return _checkout_view(order)
    if not order_matches(
        remote, order_id=order["paypal_order_id"], checkout_id=checkout_id,
        amount_cents=order["amount_cents"], currency=order["currency"],
    ):
        return _checkout_view(order)
    if remote.get("status") == "COMPLETED":
        return _confirmed_capture(db, client, order, remote)
    if remote.get("status") == "APPROVED":
        return_from_checkout(
            db, client, cfg, checkout_id=checkout_id,
            capability=_checkout_capability(cfg, checkout_id),
            paypal_order_id=order["paypal_order_id"],
        )
        return _checkout_view(store.get_order(db, checkout_id) or order)
    if remote.get("status") in {"CREATED", "PAYER_ACTION_REQUIRED"} and not order.get("approval_url"):
        try:
            href = approval_url(remote)
        except OrderError:
            return _checkout_view(order)
        order = store.finish_order_creation(
            db, checkout_id, paypal_order_id=order["paypal_order_id"], approval_url=href,
        ) or order
    return _checkout_view(order)


def score_snapshot(lead) -> tuple[int, dict]:
    """Total + per-dimension points as they are right now; stored once and never rewritten."""
    score = (getattr(lead, "evidence", None) or {}).get("score", {}) or {}
    # Keyed by the ScoreBreakdown field names (e.g. rating_score), which is what calibration reads.
    return int(lead.total_score), {v: int(score.get(v, 0) or 0) for v in DIMENSIONS.values()}


def attach_link_to_draft(db, lead_id: str, url: str, step: str = "email_1") -> bool:
    """Append the payer link to the pending email draft and send it back to 'pending', so the
    human re-approves the exact text that will go out. A sent email is never touched."""
    d = db.get_draft(lead_id, step)
    if not d or d["status"] == "sent":
        return False
    if url in d["body"]:
        return True
    body = d["body"].rstrip() + f"\n\nReview and pay securely through PayPal: {url}\n"
    db.upsert_draft(lead_id, step, d["subject"], body, status="pending", edited=True)
    return True


def create_invoice(db, client: PayPalClient, cfg: PayPalConfig, *, lead, proposal_id: str,
                   owner_id: str | None) -> dict:
    prop = store.get_proposal(db, proposal_id)
    if not prop or prop["lead_id"] != lead.lead_id:
        raise ApprovalRequired("no such proposal for this lead")
    if prop["status"] not in ("approved", "invoiced") or not prop["approved_by"]:
        raise ApprovalRequired("the proposal must be approved by a person before an invoice is created")
    items, total = normalize_line_items(prop["line_items"], max_total_cents=cfg.max_invoice_cents)
    currency = validate_currency(prop["currency"], cfg.currencies)
    if terms_hash(prop["subject"], prop["body"], items, currency) != prop["terms_hash"]:
        raise ApprovalRequired("the proposal was edited after approval; approve it again")
    if not store.claim_proposal_payment(db, proposal_id, "invoice"):
        raise ApprovalRequired("this proposal is already reserved for PayPal Orders checkout")

    total_score, breakdown = score_snapshot(lead)
    key = f"{lead.lead_id}:{proposal_id}"
    inv, created = store.claim_invoice(
        db, idempotency_key=key, lead_id=lead.lead_id, campaign_id=lead.campaign_id, owner_id=owner_id,
        proposal_id=proposal_id, amount_cents=total, currency=currency,
        score_at_send=total_score, score_breakdown=breakdown)

    if not created:
        # Double click / retry. A finished invoice is returned as-is; a half-finished DRAFT is resumed.
        if inv["status"] == "DRAFT" and inv["paypal_invoice_id"]:
            return _send_existing(db, client, inv, lead.lead_id, replay=True)
        if inv["status"] == "PENDING":
            raise InvoiceInProgress("an invoice for this proposal is already being created")
        return {"invoice": inv, "idempotent_replay": True, "link_attached": bool(inv.get("payer_url"))}

    try:
        number = new_invoice_number(lead.lead_id)
        payload = build_invoice_payload(
            invoice_number=number, currency=currency, line_items=items,
            recipient_company=lead.company_name, recipient_email=lead.contact_email or "",
            note=f"Proposal: {prop['subject']}", invoicer_business_name=cfg.invoicer_business_name)
        draft = client.create_draft_invoice(payload, request_id=key)
    except (PayPalError, InvoiceError):
        store.fail_invoice(db, inv["invoice_id"])
        raise
    inv = store.finish_invoice(db, inv["invoice_id"], paypal_invoice_id=draft["invoice_id"],
                               invoice_number=number, status="DRAFT", payer_url=None, sent_at=None)
    store.mark_proposal_invoiced(db, proposal_id)
    return _send_existing(db, client, inv, lead.lead_id)


def _send_existing(db, client: PayPalClient, inv: dict, lead_id: str, replay: bool = False) -> dict:
    from gtm_engine.paypal.store import now_iso
    # send_to_recipient=False: the link travels in Qualifyr's own human-approved email instead.
    client.send_invoice(inv["paypal_invoice_id"], send_to_recipient=False)
    details = client.get_invoice(inv["paypal_invoice_id"])
    url = payer_link(details)
    inv = store.finish_invoice(db, inv["invoice_id"], paypal_invoice_id=inv["paypal_invoice_id"],
                               invoice_number=inv["invoice_number"], status="SENT", payer_url=url, sent_at=now_iso())
    attached = bool(url) and attach_link_to_draft(db, lead_id, url)
    try:
        db.add_event(lead_id, "invoice_created", detail=inv["paypal_invoice_id"])
    except Exception:  # noqa: BLE001 - audit log only
        pass
    return {"invoice": inv, "idempotent_replay": replay, "link_attached": attached}
