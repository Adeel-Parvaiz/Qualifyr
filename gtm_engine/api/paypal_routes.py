"""PayPal lead-to-cash API routes (mounted by api/main.py via `build_router`).

Dependencies on main.py's helpers (db factory, tenant guards, campaign loader, LLM builder) are
passed in rather than imported, which avoids a circular import and keeps these routes under
exactly the same multi-tenant guards as the rest of the API.

Auth: every route is protected by the app-wide `verify_request` dependency EXCEPT
`POST /webhooks/paypal`, which is listed in auth.PUBLIC_PATHS because PayPal cannot send a user
token - it is protected by PayPal's signature verification instead (fail-closed).
"""

from __future__ import annotations

import logging
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from gtm_engine.paypal import agent_tools, calibration, seed, service, store, webhooks
from gtm_engine.paypal.client import PayPalClient, PayPalError
from gtm_engine.paypal.config import PayPalConfig, load_config
from gtm_engine.paypal.invoices import InvoiceError, normalize_line_items, terms_hash, to_cents, validate_currency
from gtm_engine.paypal.proposal import draft_proposal

log = logging.getLogger(__name__)


class ProposalRequest(BaseModel):
    base_price: float = Field(gt=0, description="Your base price for this offer (major units)")
    currency: str = "USD"
    seller_name: str = ""


class ProposalEdit(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20000)
    currency: str = "USD"
    line_items: list[dict]


class InvoiceRequest(BaseModel):
    proposal_id: str


class CalibrationRunRequest(BaseModel):
    campaign_id: str
    include_synthetic: bool = True


class SeedRequest(BaseModel):
    campaign_id: str
    count: int = Field(default=60, ge=1, le=200)


class ToolCall(BaseModel):
    arguments: dict = Field(default_factory=dict)


def build_router(*, get_db: Callable, require_lead_access: Callable, check_campaign: Callable,
                 current_user_id: Callable, get_campaign: Callable, get_llm: Callable) -> APIRouter:
    router = APIRouter()

    def cfg() -> PayPalConfig:
        return load_config()

    def client(c: PayPalConfig) -> PayPalClient:
        try:
            return PayPalClient(c)
        except PayPalError as exc:
            raise HTTPException(exc.status, str(exc))

    def paypal_call(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except PayPalError as exc:
            raise HTTPException(exc.status if exc.status in (422, 503) else 502, str(exc))
        except InvoiceError as exc:
            raise HTTPException(422, str(exc))

    def invoice_for(db, invoice_id: str, user_id):
        inv = store.get_invoice(db, invoice_id)
        if inv is None:
            raise HTTPException(404, "invoice not found")
        check_campaign(inv["campaign_id"], user_id)
        return inv

    # ---- status -----------------------------------------------------------------
    @router.get("/paypal/status")
    def paypal_status() -> dict:
        c = cfg()
        return {**c.public_status(), "llm": get_llm() is not None}

    # ---- proposals ----------------------------------------------------------------
    @router.get("/leads/{lead_id}/proposal", dependencies=[Depends(require_lead_access)])
    def get_proposal(lead_id: str) -> dict:
        db = get_db()
        try:
            return {"proposal": store.latest_proposal(db, lead_id), "invoice": store.latest_invoice_for_lead(db, lead_id)}
        finally:
            db.close()

    @router.post("/leads/{lead_id}/proposal", dependencies=[Depends(require_lead_access)])
    async def create_proposal(lead_id: str, body: ProposalRequest, user_id: str | None = Depends(current_user_id)) -> dict:
        c = cfg()
        db = get_db()
        try:
            lead = db.get_lead(lead_id)
            if not lead:
                raise HTTPException(404, "lead not found")
            try:
                currency = validate_currency(body.currency, c.currencies)
                base = to_cents(f"{body.base_price:.2f}")
                if base < 100 or base > c.max_invoice_cents:
                    raise InvoiceError("base price is outside the allowed range")
            except InvoiceError as exc:
                raise HTTPException(422, str(exc))
            campaign = get_campaign(lead.campaign_id)
            draft = await draft_proposal(get_llm(), lead.model_dump(mode="json"), campaign.offer,
                                         body.seller_name or c.invoicer_business_name, base, currency)
            return {"proposal": store.insert_proposal(db, lead_id=lead_id, campaign_id=lead.campaign_id,
                                                      owner_id=user_id, draft=draft)}
        finally:
            db.close()

    @router.put("/leads/{lead_id}/proposal/{proposal_id}", dependencies=[Depends(require_lead_access)])
    def edit_proposal(lead_id: str, proposal_id: str, body: ProposalEdit) -> dict:
        c = cfg()
        db = get_db()
        try:
            prop = store.get_proposal(db, proposal_id)
            if not prop or prop["lead_id"] != lead_id:
                raise HTTPException(404, "proposal not found")
            if prop["status"] == "invoiced":
                raise HTTPException(409, "an invoice was already created from this proposal")
            if "{" in body.subject + body.body and "}" in body.subject + body.body:
                raise HTTPException(422, "unfilled {placeholder} in proposal")
            try:
                currency = validate_currency(body.currency, c.currencies)
                items, total = normalize_line_items(body.line_items, max_total_cents=c.max_invoice_cents)
            except InvoiceError as exc:
                raise HTTPException(422, str(exc))
            return {"proposal": store.update_proposal_terms(
                db, proposal_id, subject=body.subject.strip(), body=body.body, line_items=items,
                currency=currency, amount_cents=total)}
        finally:
            db.close()

    @router.post("/leads/{lead_id}/proposal/{proposal_id}/approve", dependencies=[Depends(require_lead_access)])
    def approve_proposal(lead_id: str, proposal_id: str, user_id: str | None = Depends(current_user_id)) -> dict:
        """THE human approval gate for billing: records who approved and a fingerprint of the exact
        subject, body, line items and currency they saw."""
        c = cfg()
        db = get_db()
        try:
            prop = store.get_proposal(db, proposal_id)
            if not prop or prop["lead_id"] != lead_id:
                raise HTTPException(404, "proposal not found")
            if prop["status"] == "invoiced":
                raise HTTPException(409, "an invoice was already created from this proposal")
            try:
                items, _ = normalize_line_items(prop["line_items"], max_total_cents=c.max_invoice_cents)
                currency = validate_currency(prop["currency"], c.currencies)
            except InvoiceError as exc:
                raise HTTPException(422, str(exc))
            h = terms_hash(prop["subject"], prop["body"], items, currency)
            return {"proposal": store.approve_proposal(db, proposal_id, user_id or "local-operator", h)}
        finally:
            db.close()

    # ---- invoices ---------------------------------------------------------------
    @router.post("/leads/{lead_id}/invoice", dependencies=[Depends(require_lead_access)])
    def create_invoice(lead_id: str, body: InvoiceRequest, user_id: str | None = Depends(current_user_id)) -> dict:
        c = cfg()
        pp = client(c)
        db = get_db()
        try:
            lead = db.get_lead(lead_id)
            if not lead:
                raise HTTPException(404, "lead not found")
            try:
                return paypal_call(service.create_invoice, db, pp, c, lead=lead,
                                   proposal_id=body.proposal_id, owner_id=user_id)
            except service.ApprovalRequired as exc:
                raise HTTPException(409, str(exc))
            except service.InvoiceInProgress as exc:
                raise HTTPException(409, str(exc))
        finally:
            db.close()

    @router.get("/invoices")
    def list_invoices(campaign_id: str | None = None, user_id: str | None = Depends(current_user_id)) -> dict:
        db = get_db()
        try:
            if campaign_id:
                check_campaign(campaign_id, user_id)
            return {"invoices": store.list_invoices(db, user_id, campaign_id)}
        finally:
            db.close()

    @router.post("/invoices/{invoice_id}/refresh")
    def refresh_invoice(invoice_id: str, user_id: str | None = Depends(current_user_id)) -> dict:
        """Pull-based reconcile, for when a webhook is delayed or not configured."""
        c = cfg()
        db = get_db()
        try:
            inv = invoice_for(db, invoice_id, user_id)
            if inv["is_synthetic"] or not inv["paypal_invoice_id"]:
                return {"invoice": inv, "changed": False}
            res = webhooks.sync_from_details(db, inv, paypal_call(client(c).get_invoice, inv["paypal_invoice_id"]))
            return {"invoice": store.get_invoice(db, invoice_id), **res}
        finally:
            db.close()

    @router.post("/invoices/{invoice_id}/cancel")
    def cancel_invoice(invoice_id: str, user_id: str | None = Depends(current_user_id)) -> dict:
        c = cfg()
        db = get_db()
        try:
            inv = invoice_for(db, invoice_id, user_id)
            if inv["status"] not in ("SENT", "DRAFT"):
                raise HTTPException(409, f"a {inv['status']} invoice cannot be cancelled")
            if inv["is_synthetic"]:
                raise HTTPException(409, "synthetic demo invoices cannot be cancelled")
            paypal_call(client(c).cancel_invoice, inv["paypal_invoice_id"])
            store.set_invoice_status(db, invoice_id, "CANCELLED", store.now_iso())
            return {"invoice": store.get_invoice(db, invoice_id)}
        finally:
            db.close()

    @router.get("/invoices/{invoice_id}/events")
    def invoice_events(invoice_id: str, user_id: str | None = Depends(current_user_id)) -> dict:
        db = get_db()
        try:
            inv = invoice_for(db, invoice_id, user_id)
            return {"events": store.events_for_invoice(db, inv["paypal_invoice_id"]) if inv["paypal_invoice_id"] else []}
        finally:
            db.close()

    # ---- webhook (public; signature-verified) ------------------------------------
    @router.post("/webhooks/paypal")
    async def paypal_webhook(request: Request) -> dict:
        import json
        c = cfg()
        raw = await request.body()
        if not c.webhook_configured:
            # 503 rather than 200: PayPal retries, so no event is lost while the operator fixes config.
            raise HTTPException(503, "webhook id not configured")
        pp = client(c)
        if not pp.verify_webhook_signature(request.headers, raw):
            raise HTTPException(401, "invalid webhook signature")
        try:
            event = json.loads(raw.decode("utf-8"))
        except ValueError:
            raise HTTPException(400, "malformed event")
        db = get_db()
        try:
            return webhooks.process_event(db, event, raw.decode("utf-8"))
        finally:
            db.close()

    # ---- revenue + calibration ------------------------------------------------------
    @router.get("/revenue")
    def revenue(campaign_id: str | None = None, user_id: str | None = Depends(current_user_id)) -> dict:
        db = get_db()
        try:
            if campaign_id:
                check_campaign(campaign_id, user_id)
            out = store.revenue_summary(db, user_id, campaign_id)
            if campaign_id:
                rows = store.calibration_outcomes(db, user_id, campaign_id)
                out["score_bands"] = calibration.score_band_rates(
                    [calibration.Outcome(r["paid"], r["total"], r["breakdown"]) for r in rows])
            else:
                out["score_bands"] = []
            return out
        finally:
            db.close()

    def current_weights(db, campaign_id: str) -> dict:
        try:
            return get_campaign(campaign_id).weights.model_dump()
        except Exception:  # noqa: BLE001
            return dict(calibration.DEFAULT_WEIGHTS)

    @router.post("/calibration/run")
    def calibration_run(body: CalibrationRunRequest, user_id: str | None = Depends(current_user_id)) -> dict:
        check_campaign(body.campaign_id, user_id)
        db = get_db()
        try:
            rows = store.calibration_outcomes(db, user_id, body.campaign_id, body.include_synthetic)
            outcomes = [calibration.Outcome(r["paid"], r["total"], r["breakdown"]) for r in rows]
            result = calibration.analyse(outcomes, current_weights(db, body.campaign_id))
            synthetic = any(r["is_synthetic"] for r in rows)
            result["includes_synthetic"] = synthetic
            return {"run": store.save_calibration_run(db, campaign_id=body.campaign_id, owner_id=user_id,
                                                      result=result, includes_synthetic=synthetic)}
        finally:
            db.close()

    @router.get("/calibration")
    def calibration_list(campaign_id: str, user_id: str | None = Depends(current_user_id)) -> dict:
        check_campaign(campaign_id, user_id)
        db = get_db()
        try:
            return {"runs": store.list_calibration_runs(db, user_id, campaign_id),
                    "current_weights": current_weights(db, campaign_id)}
        finally:
            db.close()

    @router.post("/calibration/{run_id}/apply")
    def calibration_apply(run_id: str, user_id: str | None = Depends(current_user_id)) -> dict:
        """Accepting writes the proposed weights into the campaign config - applied to FUTURE scoring
        runs only. Existing leads and the invoice ledger keep their original score snapshots."""
        db = get_db()
        try:
            run = store.get_calibration_run(db, run_id)
            if not run:
                raise HTTPException(404, "calibration run not found")
            check_campaign(run["campaign_id"], user_id)
            if run["status"] != "proposed":
                raise HTTPException(409, f"this run is already {run['status']}")
            cfg_json = db.campaign_config(run["campaign_id"])
            if cfg_json is None:
                raise HTTPException(409, "this campaign is defined by a YAML file; edit its weights there")
            cfg_json["weights"] = run["result"]["proposed_weights"]
            owner = db.campaign_owner(run["campaign_id"])
            db.upsert_campaign(run["campaign_id"], cfg_json.get("name", run["campaign_id"]), cfg_json, owner)
            return {"run": store.decide_calibration_run(db, run_id, "accepted")}
        finally:
            db.close()

    @router.post("/calibration/{run_id}/reject")
    def calibration_reject(run_id: str, user_id: str | None = Depends(current_user_id)) -> dict:
        db = get_db()
        try:
            run = store.get_calibration_run(db, run_id)
            if not run:
                raise HTTPException(404, "calibration run not found")
            check_campaign(run["campaign_id"], user_id)
            return {"run": store.decide_calibration_run(db, run_id, "rejected")}
        finally:
            db.close()

    # ---- demo data -------------------------------------------------------------------
    @router.post("/demo/seed")
    def demo_seed(body: SeedRequest, user_id: str | None = Depends(current_user_id)) -> dict:
        check_campaign(body.campaign_id, user_id)
        db = get_db()
        try:
            return {"seeded": seed.seed(db, campaign_id=body.campaign_id, owner_id=user_id, count=body.count),
                    "synthetic": True}
        finally:
            db.close()

    @router.delete("/demo/seed")
    def demo_clear(campaign_id: str, user_id: str | None = Depends(current_user_id)) -> dict:
        check_campaign(campaign_id, user_id)
        db = get_db()
        try:
            return {"removed": seed.clear(db, campaign_id=campaign_id, owner_id=user_id)}
        finally:
            db.close()

    # ---- agent tools -------------------------------------------------------------------
    @router.get("/agent/tools")
    def agent_tool_specs() -> dict:
        return {"tools": agent_tools.TOOLS}

    @router.post("/agent/tools/{name}")
    def agent_tool_call(name: str, body: ToolCall, user_id: str | None = Depends(current_user_id)) -> dict:
        c = cfg()
        db = get_db()
        try:
            return agent_tools.call_tool(
                name, body.arguments, db=db, client=client(c), cfg=c, get_lead=db.get_lead,
                owner_id=user_id, may_access=lambda cid: check_campaign(cid, user_id))
        finally:
            db.close()

    return router
