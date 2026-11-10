"""N7 - Agentic payment tooling.

Two tools an LLM agent can call, described as plain JSON-schema so they drop into Anthropic
tool-use, OpenAI function calling or an MCP server unchanged:

  create_invoice(lead_id, proposal_id)   - only works once a human approval record exists
  get_invoice_status(invoice_id)         - read-only; reconciles from PayPal

The safety gate is enforced inside `service.create_invoice`, not in the prompt: an agent that
is jailbroken, confused or simply over-eager still cannot bill anyone without a person's
approval of the exact terms. (PayPal also publishes an official Agent Toolkit / MCP server; these
wrappers are the same idea with Qualifyr's approval gate and score ledger in front.)
"""

from __future__ import annotations

from gtm_engine.paypal import service, store, webhooks

TOOLS = [
    {
        "name": "create_invoice",
        "description": ("Create and send a PayPal invoice for an APPROVED proposal and attach the payer link "
                        "to the lead's email draft. Fails unless a person has approved the proposal's current "
                        "terms. Idempotent: calling twice returns the same invoice."),
        "input_schema": {
            "type": "object",
            "properties": {"lead_id": {"type": "string"}, "proposal_id": {"type": "string"}},
            "required": ["lead_id", "proposal_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_invoice_status",
        "description": "Return the current status of an invoice (DRAFT, SENT, PAID, CANCELLED, REFUNDED).",
        "input_schema": {
            "type": "object",
            "properties": {"invoice_id": {"type": "string"}},
            "required": ["invoice_id"],
            "additionalProperties": False,
        },
    },
]


def call_tool(name: str, args: dict, *, db, client, cfg, get_lead, owner_id, may_access) -> dict:
    """Dispatch one tool call. `get_lead(lead_id)` returns a Lead or None; `may_access(campaign_id)`
    raises if the caller may not see it - tools get exactly the same tenant scoping as the API."""
    if name == "create_invoice":
        lead = get_lead(args.get("lead_id", ""))
        if lead is None:
            return {"ok": False, "error": "lead not found"}
        may_access(lead.campaign_id)
        try:
            out = service.create_invoice(db, client, cfg, lead=lead, proposal_id=args.get("proposal_id", ""),
                                         owner_id=owner_id)
        except service.ApprovalRequired as exc:
            return {"ok": False, "error": "approval_required", "detail": str(exc)}
        inv = out["invoice"]
        return {"ok": True, "invoice_id": inv["invoice_id"], "status": inv["status"],
                "payer_url": inv.get("payer_url"), "idempotent_replay": out["idempotent_replay"]}
    if name == "get_invoice_status":
        inv = store.get_invoice(db, args.get("invoice_id", ""))
        if inv is None:
            return {"ok": False, "error": "invoice not found"}
        may_access(inv["campaign_id"])
        if inv["paypal_invoice_id"] and not inv["is_synthetic"]:
            webhooks.sync_from_details(db, inv, client.get_invoice(inv["paypal_invoice_id"]))
            inv = store.get_invoice(db, inv["invoice_id"])
        return {"ok": True, "invoice_id": inv["invoice_id"], "status": inv["status"],
                "amount": inv["amount_cents"] / 100, "currency": inv["currency"]}
    return {"ok": False, "error": f"unknown tool {name!r}"}
