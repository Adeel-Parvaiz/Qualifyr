"""Pure helpers for PayPal Orders v2 checkout."""

from __future__ import annotations

from urllib.parse import urlsplit

from gtm_engine.paypal.invoices import InvoiceError, cents_to_str, to_cents


class OrderError(ValueError):
    """An Orders request or response does not match the approved checkout."""


def _checkout_url(value: str, label: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise OrderError(f"{label} is invalid") from exc
    if (
        parsed.scheme != "https"
        and not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"})
    ) or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or (
        parsed.hostname not in {"localhost", "127.0.0.1"} and port not in (None, 443)
    ):
        raise OrderError(f"{label} must be an HTTPS URL (localhost is allowed for development)")
    return value


def build_order_payload(
    *,
    checkout_id: str,
    subject: str,
    line_items: list[dict],
    amount_cents: int,
    currency: str,
    return_url: str,
    cancel_url: str,
) -> dict:
    """Build the single-purchase-unit CAPTURE request from server-approved values."""
    items = []
    for item in line_items:
        entry = {
            "name": item["name"][:127],
            "quantity": str(item["quantity"]),
            "unit_amount": {
                "currency_code": currency,
                "value": cents_to_str(item["unit_amount_cents"]),
            },
        }
        if item["description"]:
            entry["description"] = item["description"][:2048]
        items.append(entry)
    amount = {"currency_code": currency, "value": cents_to_str(amount_cents)}
    if items:
        amount["breakdown"] = {
            "item_total": {"currency_code": currency, "value": cents_to_str(amount_cents)}
        }
    return {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "reference_id": checkout_id,
                "custom_id": checkout_id,
                "description": subject[:127],
                "amount": amount,
                "items": items,
            }
        ],
        "payment_source": {
            "paypal": {
                "experience_context": {
                    "return_url": _checkout_url(return_url, "checkout return URL"),
                    "cancel_url": _checkout_url(cancel_url, "checkout cancel URL"),
                    "shipping_preference": "NO_SHIPPING",
                    "user_action": "PAY_NOW",
                }
            }
        },
    }


def approval_url(order: dict) -> str:
    order_id = order.get("id")
    if not isinstance(order_id, str) or not order_id:
        raise OrderError("PayPal did not return an order ID")
    links = order.get("links")
    if not isinstance(links, list):
        raise OrderError("PayPal did not return an order approval link")
    for link in links:
        if not isinstance(link, dict) or link.get("rel") != "approve":
            continue
        href = link.get("href")
        if not isinstance(href, str):
            continue
        parsed = urlsplit(href)
        try:
            port = parsed.port
        except ValueError:
            continue
        if (
            parsed.scheme == "https"
            and parsed.hostname == "www.sandbox.paypal.com"
            and port in (None, 443)
            and not parsed.username
            and not parsed.password
            and parsed.path.rstrip("/") == "/checkoutnow"
        ):
            return href
    raise OrderError("PayPal did not return a valid Sandbox approval link")


def _matches_amount(resource: dict, amount_cents: int, currency: str) -> bool:
    amount = resource.get("amount") if isinstance(resource, dict) else None
    if not isinstance(amount, dict) or amount.get("currency_code") != currency:
        return False
    try:
        return to_cents(amount.get("value")) == amount_cents
    except InvoiceError:
        return False


def order_matches(
    order: dict,
    *,
    order_id: str,
    checkout_id: str,
    amount_cents: int,
    currency: str,
    status: str | None = None,
) -> bool:
    if (
        not isinstance(order, dict)
        or order.get("id") != order_id
        or order.get("intent") != "CAPTURE"
        or (status is not None and order.get("status") != status)
    ):
        return False
    units = order.get("purchase_units")
    return (
        isinstance(units, list)
        and len(units) == 1
        and isinstance(units[0], dict)
        and units[0].get("custom_id") == checkout_id
        and _matches_amount(units[0], amount_cents, currency)
    )


def completed_capture(
    order: dict,
    *,
    order_id: str,
    checkout_id: str,
    amount_cents: int,
    currency: str,
) -> dict | None:
    if not order_matches(
        order, order_id=order_id, checkout_id=checkout_id, amount_cents=amount_cents,
        currency=currency, status="COMPLETED",
    ):
        return None
    unit = order["purchase_units"][0]
    payments = unit.get("payments")
    if not isinstance(payments, dict):
        return None
    captures = payments.get("captures")
    if not isinstance(captures, list) or len(captures) != 1:
        return None
    capture = captures[0]
    if (
        not isinstance(capture, dict)
        or capture.get("status") != "COMPLETED"
        or not isinstance(capture.get("id"), str)
        or not capture["id"]
        or not _matches_amount(capture, amount_cents, currency)
    ):
        return None
    return capture


def captured_payment_matches(
    capture: dict,
    *,
    capture_id: str,
    order_id: str,
    amount_cents: int,
    currency: str,
) -> bool:
    if (
        not isinstance(capture, dict)
        or capture.get("id") != capture_id
        or capture.get("status") != "COMPLETED"
        or not _matches_amount(capture, amount_cents, currency)
    ):
        return False
    supplementary = capture.get("supplementary_data")
    related = supplementary.get("related_ids") if isinstance(supplementary, dict) else None
    related = related if isinstance(related, dict) else {}
    related_order_id = related.get("order_id") if isinstance(related, dict) else None
    return related_order_id == order_id
