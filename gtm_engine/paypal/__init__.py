"""Qualifyr x PayPal: lead-to-cash.

Modules (see docs/PAYPAL.md for the full picture):

  config       env-driven settings; sandbox-only unless live is double-opted-in
  client       thin PayPal REST client (OAuth, Invoicing v2, webhook verification)
  invoices     pure helpers: validation, money handling, invoice payload builder
  proposal     grounded AI proposal agent (deterministic fallback always runs)
  store        Postgres tables + repository functions (proposals, invoices, ledger)
  webhooks     event -> invoice status transitions (idempotent, monotonic)
  calibration  smoothed, bounded score-weight proposals learned from paid outcomes
  agent_tools  `create_invoice` / `get_invoice_status` behind the human-approval gate
  seed         clearly labelled synthetic sandbox outcomes for the demo

Nothing here imports at package-import time beyond the stdlib, so importing
`gtm_engine.paypal` is free.
"""
