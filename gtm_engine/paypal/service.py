"""N2 - Invoice builder service: approved proposal -> PayPal invoice -> payer link in the email draft.

The approval gate lives HERE (not only in the HTTP route) so every caller - the API, the agent
tools, a future CLI - gets the same guarantee: no invoice exists without a human approval record
whose fingerprint still matches the proposal's current terms.
"""

from __future__ import annotations

from gtm_engine.paypal import store
from gtm_engine.paypal.calibration import DIMENSIONS
from gtm_engine.paypal.client import PayPalClient, PayPalError
from gtm_engine.paypal.config import PayPalConfig
from gtm_engine.paypal.invoices import (
    InvoiceError, build_invoice_payload, new_invoice_number, normalize_line_items, payer_link,
    terms_hash, validate_currency,
)


class ApprovalRequired(Exception):
    """Raised when there is no valid human approval for the proposal (maps to HTTP 409)."""


class InvoiceInProgress(Exception):
    pass


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
