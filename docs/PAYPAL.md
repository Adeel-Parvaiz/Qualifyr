# Qualifyr x PayPal: Lead-to-Cash

Built for the PayPal AI Hackathon 2026. **Sandbox only.** No real money moves.

> A lead engine whose scoring is trained by real payments, not guesses.

## What it does

```
Discover -> Score (with reasons) -> AI drafts proposal -> YOU approve terms
   -> PayPal invoice OR one-time Orders checkout (sandbox)
   -> webhook confirms payment -> outcome ledger -> calibration proposes new weights -> YOU accept
```

Two human gates, never skipped: approving the **terms** (what gets billed) and approving the **email**
(what gets sent). Nothing is billed or sent unattended.

## Where the code lives

| Area | Path |
|---|---|
| PayPal client (OAuth, Invoicing v2, Orders v2, webhook verify) | `gtm_engine/paypal/client.py` |
| Invoice and Orders rules, money, payload builder | `gtm_engine/paypal/invoices.py`, `gtm_engine/paypal/orders.py` |
| Approval gate + payment creation and verification | `gtm_engine/paypal/service.py` |
| AI proposal agent (grounded, deterministic fallback) | `gtm_engine/paypal/proposal.py` |
| Webhook handling (idempotent, monotonic) | `gtm_engine/paypal/webhooks.py` |
| Outcome ledger + tables | `gtm_engine/paypal/store.py` |
| Score calibration engine | `gtm_engine/paypal/calibration.py` |
| Agent tools (`create_invoice`, `get_invoice_status`) | `gtm_engine/paypal/agent_tools.py` |
| Synthetic demo data | `gtm_engine/paypal/seed.py` |
| HTTP routes | `gtm_engine/api/paypal_routes.py` |
| UI | `web/src/app/(app)/revenue/`, `web/src/components/paypal-invoice-panel.tsx` |
| Tests | `tests/test_paypal.py` |

## Setup (about 10 minutes)

1. **Sandbox app.** In the PayPal Developer Dashboard create a *Sandbox* REST app. Copy Client ID and Secret.
2. **Environment variables** (see `.env.example`): `GTM_PAYPAL_CLIENT_ID`, `GTM_PAYPAL_CLIENT_SECRET`,
   `GTM_PAYPAL_CHECKOUT_RETURN_URL`, and `GTM_PAYPAL_CHECKOUT_CANCEL_URL`.
   On Vercel, add them as runtime (not `NEXT_PUBLIC_`) variables and redeploy.
3. **Webhook.** In the same app add a webhook to `https://<your-host>/api/webhooks/paypal` for
   invoice events and `CHECKOUT.ORDER.APPROVED`, `CHECKOUT.ORDER.COMPLETED`,
   `PAYMENT.CAPTURE.COMPLETED`. Copy its **Webhook ID** into
   `GTM_PAYPAL_WEBHOOK_ID`. (Locally, expose port 8000 with a tunnel such as ngrok.)
4. Tables are created automatically on first request (`proposals`, `invoices`, `paypal_orders`, `payment_events`, `calibration_runs`).
5. **Test accounts.** Dashboard > Testing tools > Sandbox accounts. Use the *Business* account that owns the app
   as the seller and a *Personal* sandbox account to pay: open the payer link from the invoice, log in with the personal
   sandbox account and pay. The webhook then marks the invoice PAID.

### Try it

1. Open **Outreach**, pick a lead, in the *PayPal invoice* box enter your base price and click **Draft proposal with AI**.
2. Edit if you like, **Approve terms**, then **Create PayPal invoice & attach link**.
3. Approve the email (the payer link is inside it). Open the payer link and pay with the sandbox buyer.
4. **Revenue** shows the invoice flip to Paid; **Run calibration** proposes new weights.

To try the one-time checkout instead, approve the proposal and choose **One-time PayPal checkout**.
Open the Sandbox approval link, pay with the Personal Sandbox account, and return to `/paypal/return`.
The callback captures only after PayPal reports the matching order as approved; Qualifyr verifies
the order and capture amounts/status server-side. A pending result is not shown as paid; the seller
can use **Check payment status** to reconcile and capture an already-approved order, and the signed
webhook reconciles completed captures.

No resolved invoices yet? On **Revenue**, *Load synthetic demo data* adds clearly labelled fake outcomes
(badged "synthetic", never sent to PayPal, removable with one click) so the learning loop can be shown immediately.

## Safety and correctness decisions

- **Sandbox-only**: the API base URL is hard-wired to PayPal Sandbox; live-mode variables are ignored.
- **Approval is bound to exact terms**: approval stores a hash of subject, body, line items and currency. Editing after approval invalidates it. Enforced in the service layer, so the API and the agent tools share it.
- **No double payment paths**: each approved proposal is atomically claimed by either Invoicing or Orders, never both.
- **Orders checkout**: only the server-stored approved proposal sets the amount. Stable request IDs, a per-checkout HMAC capability on the return URL (derived server-side and never stored or returned to the seller API), provider-side order reconciliation, and verified captures prevent trusting browser status or amount data.
- **PayPal never emails a prospect on its own**: invoices are sent with `send_to_recipient=false`; the link travels in your own approved email.
- **Server-side money**: integer cents, totals recomputed on the server, amount ceiling, currency allow-list (PayPal does not settle PKR/AED/SAR, so default is USD).
- **Webhook**: signature verified through PayPal's verify endpoint *before* anything is trusted; fails closed; each event id processed once; Orders state changes and event deduplication are atomic; completed captures are re-read from PayPal before the ledger marks them paid.
- **Grounded AI**: the model writes one sentence, checked against stored evidence (numbers must already appear in it). Prices are never model-invented; they come from your base price with a suggested range you confirm.
- **Calibration is advisory**: smoothed rates, minimum sample sizes, at most 5 points of movement per weight per cycle, total stays 100, explanation in plain English, applied only when you accept, and only to future scoring runs. The ledger keeps the score snapshot from send time.
- **Tenant isolation**: every route reuses the existing campaign/lead ownership guards.

## Agent tools

`GET /agent/tools` returns two JSON-schema tools (`create_invoice`, `get_invoice_status`) usable with Anthropic
tool use, OpenAI function calling or an MCP wrapper. `POST /agent/tools/{name}` executes one. `create_invoice` fails unless a person
has approved the proposal's current terms, regardless of what the agent was told.

## Using the APIMatic Context Plugin

The Context Plugin is a developer tool: an MCP server that gives your AI coding agent current, SDK-accurate PayPal
context. Install it in the IDE you build with:

- **Claude Code:** `/plugin marketplace add apimatic/context-matic` then `/plugin install context-matic@apimatic`, reload plugins, run `/onboard-context-matic`.
- **Cursor / VS Code:** one-click install from apimatic.io/product/contextmatic (or search `@agentPlugins context-matic` in VS Code).

Coverage note: at the time of writing, the PayPal Server SDK surface exposed through the plugin covers Orders, Payments,
Vault, Transaction Search and Subscriptions. Invoicing may not be included, so this repo calls the Invoicing v2 REST API directly
(`client.py`). Ask the plugin what it covers (`/onboard-context-matic`) and **record honestly what you used it for** in your Devpost text.

Suggested, genuinely useful prompts (run them in your IDE with the plugin enabled):

1. *"Using the PayPal Python Server SDK, add a Transaction Search reconciliation job that cross-checks paid invoices against PayPal transactions for the sandbox account."*
2. *"Using the PayPal Server SDK, add an Orders v2 'pay now' fallback for leads whose email cannot receive an invoice."*
3. *"Review `gtm_engine/paypal/client.py` against the current PayPal SDK and API docs and list anything outdated."*

If you adopt the SDK for any call, keep the existing approval gate and idempotency in `service.py`.

## Limits worth knowing

- Verified by mocked unit tests (`tests/test_paypal.py`): money, validation, calibration, grounding, webhook logic, signature-call shape, and Orders request behavior.
- **Not yet exercised against PayPal Sandbox or Postgres.** Run a sandbox buyer checkout and confirm the webhook events in the Developer Dashboard before claiming end-to-end integration.
- PayPal's "viewed" state is not handled; only CREATED / SENT / PAID / CANCELLED / REFUNDED are.
