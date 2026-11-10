# PayPal Orders Checkout Plan

## Goal

Add a PayPal Orders v2 checkout path for an already-approved proposal while preserving
the current invoice path. A buyer approves payment on PayPal; the application captures
only after approval and records the resulting PayPal transaction. No price, product,
or currency is invented: checkout terms come from the human-approved proposal.

## Verified API contract

- The hackathon rules require a functional app using PayPal's developer platform and an
  AI tool; they do not mandate Orders or a specific payment API.
- Context-Matic confirms Orders v2 `POST /v2/checkout/orders` uses `intent` and
  `purchase_units`, `POST /v2/checkout/orders/{id}/capture` requires the order ID, and
  capture is permitted only after buyer approval unless a valid payment source is
  supplied. The create response's `approve` HATEOAS link is the buyer redirect.
- Context-Matic confirms the order statuses include `APPROVED` and `COMPLETED`, and
  capture statuses include `COMPLETED`, `PENDING`, `DECLINED`, and `FAILED`. The
  `COMPLETED` capture response and a subsequent PayPal order read must agree with the
  approved proposal before the local ledger marks it paid.
- Context-Matic's generated Python sample uses `paypalserversdk`; the installed plugin
  SDK map identifies a different package (`paypal`). The implementation will not
  copy either SDK sample: it will use the project's existing HTTPX REST/OAuth client,
  which already calls Invoicing v2 and requires no new SDK dependency.
- PayPal documents `CHECKOUT.ORDER.APPROVED`, `CHECKOUT.ORDER.COMPLETED`, and
  `PAYMENT.CAPTURE.COMPLETED`, `PAYMENT.CAPTURE.PENDING`, `PAYMENT.CAPTURE.DENIED`,
  `PAYMENT.CAPTURE.REFUNDED`, and `PAYMENT.CAPTURE.REVERSED` webhook events. Webhook
  signatures must be verified before event processing.

## Proposed scope

1. Keep Invoicing v2 intact and add an independent one-time Orders v2 checkout path.
2. Use only a human-approved proposal's server-stored line items and currency. Reject
   amount/currency/status fields from callers and invalidate payment if approved terms
   no longer match their stored fingerprint.
3. Use Orders v2 with `CAPTURE` intent and server-configured return/cancel URLs. Persist
   order ID, capture ID, idempotency keys, and payment status before/after provider
   calls as appropriate.
4. On return, use a short-lived high-entropy checkout capability to locate the local
   order. Read the order from PayPal, verify its ID, intent, approval, and amount, then
   capture with a stable `PayPal-Request-Id`. Read the order again and mark paid only
   when PayPal reports `COMPLETED` and a matching capture has `COMPLETED` status and
   the approved amount/currency. Unknown network outcomes remain pending for safe
   reconciliation.
5. Atomically claim one payment method per proposal so it cannot be paid by both an
   invoice and an Order. Existing invoice records are accounted for when the schema is
   initialized; both payment services honor the claim.
6. Require signed webhook events and process each event ID plus its state change
   transactionally, so failed updates can be retried without losing payment state.
   Webhooks may confirm or advance payment state but never treat buyer approval alone
   as payment.
7. Keep checkout creation authenticated and tenant-scoped. The public return accepts
   only the persisted checkout capability and exposes only a safe payment status.
   PayPal cancel redirects show cancellation without falsely changing payment state.
8. Enforce sandbox-only operation. Never use live API endpoints or enable live mode.
   Keep credentials server-side, don't log or expose token/webhook bodies, and never
   allow AI to create or authorize a payment.
9. Add the separate checkout action and clear pending/success/cancel/failure UI while
   preserving the existing invoice UI. Add mocked HTTP tests; do not make real PayPal
   calls during tests.
10. Document sandbox credentials, return/cancel URLs, webhook registration, and the
    buyer/seller end-to-end steps. Do not edit actual `.env` files.

## Out of scope

- Replacing or changing the existing invoice workflow.
- Subscriptions, automatic tier upgrades, stored payment methods, card handling, or
  setting new product prices.
- Live transactions or real PayPal network calls during implementation/testing.
- Installing a PayPal SDK solely for the Orders API.

## Expected files

- `gtm_engine/paypal/config.py`, `client.py`, `service.py`, `store.py`, and `webhooks.py`;
  new `gtm_engine/paypal/orders.py`.
- `gtm_engine/api/paypal_routes.py` and `gtm_engine/api/auth.py`.
- `web/src/lib/api.ts`, `web/src/components/paypal-invoice-panel.tsx`, and a public
  PayPal return page under `web/src/app/paypal/return/`.
- `tests/test_paypal.py` and a focused Orders test module.
- `.env.example`, `docs/PAYPAL.md`, and this plan.

## Verification / completion criteria

- Existing PayPal invoice tests and behavior remain unchanged.
- Orders creation cannot happen before proposal approval and cannot substitute
  client-supplied amount/currency for approved terms.
- Capture is impossible before PayPal reports buyer approval, is safe on repeated
  callbacks, and is verified against PayPal's completed order/capture response.
- Invoice/Order payment conflicts are blocked, and duplicate signed webhook events are
  harmless without dropping a state update after a transient database failure.
- Unknown upstream outcomes remain pending/reconcilable, not success or a blind retry.
- Targeted Python tests and the required frontend build/type checks pass.
- A separate sandbox checkout/webhook verification is documented as requiring the
  user's configured sandbox credentials; no credentials are requested or exposed here.
