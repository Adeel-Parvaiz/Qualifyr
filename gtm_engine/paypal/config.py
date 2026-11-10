"""PayPal settings, read from the environment.

This integration is Sandbox-only. Credentials live in environment variables only (never in
the repo); the secret is never logged or returned by any endpoint.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

SANDBOX_BASE = "https://api-m.sandbox.paypal.com"

# PayPal does not settle every currency Qualifyr's markets use (PKR, AED and SAR are not on its
# supported list at the time of writing), so invoices default to USD. Override with
# GTM_PAYPAL_CURRENCIES (comma-separated ISO codes) after checking PayPal's currency table.
DEFAULT_CURRENCIES = ("USD", "EUR", "GBP")
DEFAULT_MAX_INVOICE_CENTS = 1_000_000  # 10,000.00 - a hard server-side ceiling


def _env(*names: str) -> str:
    """First non-empty value among the names. GTM_-prefixed names win; the unprefixed
    PAYPAL_* names that PayPal's own docs and the APIMatic plugin use are accepted too."""
    for n in names:
        v = os.environ.get(n, "").strip()
        if v:
            return v
    return ""


@dataclass(frozen=True)
class PayPalConfig:
    client_id: str
    client_secret: str
    webhook_id: str
    env: str  # "sandbox" | "live"
    currencies: tuple[str, ...]
    max_invoice_cents: int
    invoicer_business_name: str
    checkout_return_url: str = ""
    checkout_cancel_url: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    @property
    def webhook_configured(self) -> bool:
        return bool(self.webhook_id)

    @property
    def is_sandbox(self) -> bool:
        return True

    @property
    def base_url(self) -> str:
        return SANDBOX_BASE

    def public_status(self) -> dict:
        """Safe-to-return view: no secrets, no ids."""
        return {
            "configured": self.configured,
            "webhook_configured": self.webhook_configured,
            "mode": "sandbox",
            "currencies": list(self.currencies),
            "max_invoice": self.max_invoice_cents / 100,
        }


def load_config() -> PayPalConfig:
    currencies = tuple(
        c.strip().upper()
        for c in (_env("GTM_PAYPAL_CURRENCIES") or ",".join(DEFAULT_CURRENCIES)).split(",")
        if c.strip()
    ) or DEFAULT_CURRENCIES

    try:
        max_cents = int(_env("GTM_PAYPAL_MAX_INVOICE_CENTS") or DEFAULT_MAX_INVOICE_CENTS)
    except ValueError:
        max_cents = DEFAULT_MAX_INVOICE_CENTS

    return PayPalConfig(
        client_id=_env("GTM_PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_ID"),
        client_secret=_env("GTM_PAYPAL_CLIENT_SECRET", "PAYPAL_CLIENT_SECRET"),
        webhook_id=_env("GTM_PAYPAL_WEBHOOK_ID", "PAYPAL_WEBHOOK_ID"),
        env="sandbox",
        currencies=currencies,
        max_invoice_cents=max(100, max_cents),
        invoicer_business_name=_env("GTM_PAYPAL_BUSINESS_NAME"),
        checkout_return_url=_env("GTM_PAYPAL_CHECKOUT_RETURN_URL"),
        checkout_cancel_url=_env("GTM_PAYPAL_CHECKOUT_CANCEL_URL"),
    )
