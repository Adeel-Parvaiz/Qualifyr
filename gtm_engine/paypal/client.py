"""Thin PayPal REST client: OAuth2 client-credentials, Invoicing v2, webhook verification.

Server-side only - the client secret never reaches the browser. httpx is imported lazily so
the pure modules stay importable (and testable) without it.
"""

from __future__ import annotations

import base64
import json
import logging
import threading
import time
import uuid
from urllib.parse import quote

from gtm_engine.paypal.config import PayPalConfig
from gtm_engine.paypal.invoices import invoice_id_from_href

log = logging.getLogger(__name__)

# Access tokens live ~9 hours. Cached per client id for the life of a warm process so a burst of
# requests does one OAuth round-trip, not one each.
_TOKENS: dict[str, tuple[str, float]] = {}
_TOKEN_LOCK = threading.Lock()


class PayPalError(Exception):
    def __init__(self, message: str, status: int = 502, debug_id: str | None = None,
                 *, provider_status: int | None = None, outcome_unknown: bool = False):
        super().__init__(message)
        self.status = status
        self.debug_id = debug_id
        self.provider_status = provider_status
        self.outcome_unknown = outcome_unknown


class PayPalClient:
    def __init__(self, cfg: PayPalConfig, *, http=None, timeout: float = 20.0):
        if not cfg.configured:
            raise PayPalError(
                "PayPal is not configured: set GTM_PAYPAL_CLIENT_ID and GTM_PAYPAL_CLIENT_SECRET "
                "(sandbox REST app credentials)", status=503)
        self.cfg = cfg
        if http is None:
            import httpx  # lazy
            http = httpx.Client(timeout=timeout)
        self.http = http

    # -- auth ---------------------------------------------------------------

    def _token(self) -> str:
        with _TOKEN_LOCK:
            cached = _TOKENS.get(self.cfg.client_id)
            if cached and cached[1] > time.time() + 60:
                return cached[0]
        basic = base64.b64encode(f"{self.cfg.client_id}:{self.cfg.client_secret}".encode()).decode()
        import httpx
        try:
            r = self.http.post(
                f"{self.cfg.base_url}/v1/oauth2/token",
                headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
                content="grant_type=client_credentials",
            )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout, httpx.ProxyError) as exc:
            raise PayPalError("PayPal authentication connection failed before the request was sent", 502) from exc
        except httpx.RequestError as exc:
            raise PayPalError("PayPal authentication request failed", 502) from exc
        if r.status_code != 200:
            raise PayPalError(f"PayPal OAuth failed ({r.status_code}); check the sandbox credentials", 502)
        try:
            data = r.json()
            token, ttl = data["access_token"], int(data.get("expires_in", 3000))
        except (ValueError, KeyError, TypeError) as exc:
            raise PayPalError("PayPal returned an invalid authentication response", 502) from exc
        if not isinstance(token, str) or not token or ttl <= 0:
            raise PayPalError("PayPal returned an invalid authentication response", 502)
        with _TOKEN_LOCK:
            _TOKENS[self.cfg.client_id] = (token, time.time() + ttl)
        return token

    def _request(self, method: str, path: str, *, body: dict | None = None,
                 request_id: str | None = None, prefer: str | None = None) -> dict:
        import httpx

        headers = {"Authorization": f"Bearer {self._token()}", "Content-Type": "application/json"}
        if request_id:
            headers["PayPal-Request-Id"] = request_id  # PayPal-side idempotency
        if prefer:
            headers["Prefer"] = prefer
        try:
            r = self.http.request(method, f"{self.cfg.base_url}{path}", headers=headers,
                                  content=json.dumps(body) if body is not None else None)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout, httpx.ProxyError) as exc:
            raise PayPalError("PayPal connection failed before the request was sent", 502) from exc
        except httpx.RequestError as exc:
            write = method in {"POST", "PUT", "PATCH"}
            raise PayPalError("PayPal request outcome could not be confirmed", 502,
                              outcome_unknown=write) from exc
        if r.status_code >= 400:
            debug_id = r.headers.get("paypal-debug-id")
            msg = ""
            try:
                j = r.json()
                msg = j.get("message") or j.get("error_description") or ""
                details = "; ".join(d.get("issue", "") for d in (j.get("details") or []) if isinstance(d, dict))
                msg = f"{msg} ({details})" if details else msg
            except Exception:  # noqa: BLE001 - not JSON
                pass
            log.warning("paypal %s %s -> %s debug_id=%s", method, path, r.status_code, debug_id)
            raise PayPalError(f"PayPal rejected the request ({r.status_code}): {msg or r.reason_phrase}",
                              status=422 if r.status_code in (400, 422) else 502, debug_id=debug_id,
                              provider_status=r.status_code,
                              outcome_unknown=method in {"POST", "PUT", "PATCH"}
                              and (r.status_code >= 500 or r.status_code in (408, 409)))
        if r.status_code == 204 or not r.content:
            return {}
        try:
            return r.json()
        except ValueError as exc:
            write = method in {"POST", "PUT", "PATCH"}
            raise PayPalError("PayPal returned an unreadable response", 502,
                              provider_status=r.status_code, outcome_unknown=write) from exc

    # -- Orders API v2 -------------------------------------------------------

    def create_order(self, payload: dict, *, request_id: str) -> dict:
        return self._request(
            "POST", "/v2/checkout/orders", body=payload, request_id=request_id,
            prefer="return=representation",
        )

    def get_order(self, order_id: str) -> dict:
        return self._request("GET", f"/v2/checkout/orders/{quote(order_id, safe='')}")

    def capture_order(self, order_id: str, *, request_id: str) -> dict:
        return self._request(
            "POST", f"/v2/checkout/orders/{quote(order_id, safe='')}/capture",
            request_id=request_id, prefer="return=representation",
        )

    def get_capture(self, capture_id: str) -> dict:
        return self._request("GET", f"/v2/payments/captures/{quote(capture_id, safe='')}")

    # -- Invoicing API v2 ----------------------------------------------------

    def create_draft_invoice(self, payload: dict, request_id: str | None = None) -> dict:
        """Returns {"invoice_id", "href"}. PayPal answers with a HATEOAS link only."""
        data = self._request("POST", "/v2/invoicing/invoices", body=payload,
                             request_id=request_id or str(uuid.uuid4()))
        href = data.get("href", "")
        invoice_id = data.get("id") or invoice_id_from_href(href)
        if not invoice_id:
            raise PayPalError("PayPal did not return an invoice id", 502)
        return {"invoice_id": invoice_id, "href": href}

    def send_invoice(self, invoice_id: str, *, send_to_recipient: bool = False,
                     subject: str | None = None, note: str | None = None) -> dict:
        """Moves a draft to SENT. We default to send_to_recipient=False: the payer link is
        delivered inside Qualifyr's own human-approved email, so PayPal never mails a prospect
        on its own."""
        body: dict = {"send_to_recipient": send_to_recipient, "send_to_invoicer": False}
        if subject:
            body["subject"] = subject[:200]
        if note:
            body["note"] = note[:4000]
        return self._request("POST", f"/v2/invoicing/invoices/{invoice_id}/send", body=body,
                             request_id=f"send-{invoice_id}")

    def get_invoice(self, invoice_id: str) -> dict:
        return self._request("GET", f"/v2/invoicing/invoices/{invoice_id}")

    def cancel_invoice(self, invoice_id: str, *, note: str = "Cancelled by the sender") -> dict:
        return self._request("POST", f"/v2/invoicing/invoices/{invoice_id}/cancel",
                             body={"note": note[:4000], "send_to_invoicer": False, "send_to_recipient": False})

    # -- webhooks ------------------------------------------------------------

    def verify_webhook_signature(self, headers, raw_body: bytes) -> bool:
        """Ask PayPal to verify the event signature. Fails closed: any problem -> False.

        The webhook_event is spliced in as the *raw* body text rather than re-serialised, since
        PayPal checks the signature against the exact bytes it sent."""
        if not self.cfg.webhook_configured:
            return False
        h = {k.lower(): v for k, v in headers.items()}
        needed = ("paypal-auth-algo", "paypal-cert-url", "paypal-transmission-id",
                  "paypal-transmission-sig", "paypal-transmission-time")
        if any(not h.get(k) for k in needed):
            return False
        try:
            event_text = raw_body.decode("utf-8")
            body = (
                '{"auth_algo":%s,"cert_url":%s,"transmission_id":%s,"transmission_sig":%s,'
                '"transmission_time":%s,"webhook_id":%s,"webhook_event":%s}'
            ) % (
                json.dumps(h["paypal-auth-algo"]), json.dumps(h["paypal-cert-url"]),
                json.dumps(h["paypal-transmission-id"]), json.dumps(h["paypal-transmission-sig"]),
                json.dumps(h["paypal-transmission-time"]), json.dumps(self.cfg.webhook_id), event_text,
            )
            r = self.http.post(
                f"{self.cfg.base_url}/v1/notifications/verify-webhook-signature",
                headers={"Authorization": f"Bearer {self._token()}", "Content-Type": "application/json"},
                content=body,
            )
            return r.status_code == 200 and r.json().get("verification_status") == "SUCCESS"
        except Exception as exc:  # noqa: BLE001 - verification must never fail open
            log.warning("webhook verification error: %s", exc)
            return False
