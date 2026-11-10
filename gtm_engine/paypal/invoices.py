"""Pure invoice helpers: money, validation, payload construction. No I/O, stdlib only.

Money is held as integer minor units (cents) everywhere inside Qualifyr and only formatted to
PayPal's decimal string at the edge, so there is no float drift between the amount the human
approved, the amount stored in the ledger and the amount PayPal bills.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone

MAX_ITEMS = 20
MAX_QTY = 1000


class InvoiceError(ValueError):
    """A proposal or invoice failed server-side validation (maps to HTTP 422)."""


def cents_to_str(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(int(cents))
    return f"{sign}{cents // 100}.{cents % 100:02d}"


def to_cents(value) -> int:
    """Parse a user-entered amount ('120', '120.5', 120.5) into integer cents, rejecting
    anything with more than two decimals instead of silently rounding money."""
    if isinstance(value, bool):
        raise InvoiceError("amount must be a number")
    s = str(value).strip().replace(",", "")
    if not re.fullmatch(r"\d+(\.\d{1,2})?", s):
        raise InvoiceError(f"invalid amount: {value!r}")
    whole, _, frac = s.partition(".")
    return int(whole) * 100 + int((frac + "00")[:2])


def normalize_line_items(items, *, max_total_cents: int) -> tuple[list[dict], int]:
    """Validate line items and return (clean_items, total_cents).

    Each item: name, description, quantity (int), unit_amount_cents (int). The total is always
    recomputed here; a client-supplied total is never trusted.
    """
    if not isinstance(items, list) or not items:
        raise InvoiceError("at least one line item is required")
    if len(items) > MAX_ITEMS:
        raise InvoiceError(f"at most {MAX_ITEMS} line items")
    clean: list[dict] = []
    total = 0
    for i, raw in enumerate(items, 1):
        if not isinstance(raw, dict):
            raise InvoiceError(f"line item {i} is malformed")
        name = " ".join(str(raw.get("name", "")).split())[:200]
        if not name:
            raise InvoiceError(f"line item {i} needs a name")
        desc = " ".join(str(raw.get("description", "") or "").split())[:1000]
        try:
            qty = int(raw.get("quantity", 1))
        except (TypeError, ValueError):
            raise InvoiceError(f"line item {i}: quantity must be a whole number")
        if not 1 <= qty <= MAX_QTY:
            raise InvoiceError(f"line item {i}: quantity must be 1-{MAX_QTY}")
        if "unit_amount_cents" in raw:
            try:
                unit = int(raw["unit_amount_cents"])
            except (TypeError, ValueError):
                raise InvoiceError(f"line item {i}: bad amount")
        else:
            unit = to_cents(raw.get("unit_amount", ""))
        if unit < 1:
            raise InvoiceError(f"line item {i}: amount must be positive")
        total += unit * qty
        clean.append({"name": name, "description": desc, "quantity": qty, "unit_amount_cents": unit})
    if total < 100:
        raise InvoiceError("invoice total must be at least 1.00")
    if total > max_total_cents:
        raise InvoiceError(f"invoice total exceeds the {cents_to_str(max_total_cents)} limit")
    return clean, total


def validate_currency(currency: str, allowed: tuple[str, ...]) -> str:
    c = (currency or "").strip().upper()
    if c not in allowed:
        raise InvoiceError(f"currency {c or '?'} is not enabled (allowed: {', '.join(allowed)})")
    return c


def terms_hash(subject: str, body: str, line_items: list[dict], currency: str) -> str:
    """Fingerprint of exactly what the human approved. The invoice endpoint recomputes it, so
    an edit made after approval invalidates the approval."""
    blob = json.dumps(
        {"s": subject, "b": body, "i": line_items, "c": currency},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def new_invoice_number(lead_id: str, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    tail = re.sub(r"[^A-Za-z0-9]", "", lead_id)[-6:].upper() or "LEAD"
    return f"QLF-{now:%y%m%d}-{tail}-{now:%H%M%S}"


def build_invoice_payload(
    *, invoice_number: str, currency: str, line_items: list[dict], recipient_company: str,
    recipient_email: str, note: str = "", due_in_days: int = 14, today: date | None = None,
    invoicer_business_name: str = "",
) -> dict:
    """PayPal Invoicing v2 'create draft invoice' body."""
    if not recipient_email or "@" not in recipient_email:
        raise InvoiceError("the lead has no usable contact email to invoice")
    today = today or datetime.now(timezone.utc).date()
    payload: dict = {
        "detail": {
            "invoice_number": invoice_number,
            "invoice_date": today.isoformat(),
            "currency_code": currency,
            "note": note[:4000],
            "payment_term": {
                "term_type": "DUE_ON_DATE_SPECIFIED",
                "due_date": (today + timedelta(days=max(1, due_in_days))).isoformat(),
            },
        },
        "primary_recipients": [{
            "billing_info": {
                "name": {"business_name": recipient_company[:300] or recipient_email},
                "email_address": recipient_email,
            }
        }],
        "items": [
            {
                "name": it["name"],
                "description": it["description"],
                "quantity": str(it["quantity"]),
                "unit_amount": {"currency_code": currency, "value": cents_to_str(it["unit_amount_cents"])},
            }
            for it in line_items
        ],
        "configuration": {"allow_tip": False, "tax_inclusive": False},
    }
    if invoicer_business_name:
        payload["invoicer"] = {"name": {"business_name": invoicer_business_name[:300]}}
    return payload


def invoice_id_from_href(href: str) -> str | None:
    """The create-draft response is a HATEOAS link (…/v2/invoicing/invoices/INV2-XXXX)."""
    if not href:
        return None
    tail = href.rstrip("/").rsplit("/", 1)[-1]
    return tail or None


def payer_link(details: dict) -> str | None:
    """The hosted payment page a buyer opens. Prefer the documented metadata field; fall back to
    a 'recipient-view' HATEOAS link."""
    meta = ((details or {}).get("detail") or {}).get("metadata") or {}
    url = meta.get("recipient_view_url")
    if url:
        return url
    for link in (details or {}).get("links") or []:
        if "recipient" in str(link.get("rel", "")):
            return link.get("href")
    return None
