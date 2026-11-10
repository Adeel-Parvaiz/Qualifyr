"""Unit tests for the PayPal lead-to-cash modules. Pure logic: no database, no network."""

import asyncio
import json
from datetime import date

import pytest

from gtm_engine.paypal import calibration, proposal, seed, webhooks
from gtm_engine.paypal.client import PayPalClient
from gtm_engine.paypal.config import PayPalConfig, load_config
from gtm_engine.paypal.invoices import (
    InvoiceError, build_invoice_payload, cents_to_str, invoice_id_from_href, normalize_line_items,
    payer_link, terms_hash, to_cents, validate_currency,
)


# ---------------------------------------------------------------- money & validation
def test_to_cents_and_back():
    assert to_cents("120") == 12000
    assert to_cents("120.5") == 12050
    assert to_cents(99.99) == 9999
    assert cents_to_str(12050) == "120.50"
    assert cents_to_str(5) == "0.05"


@pytest.mark.parametrize("bad", ["12.345", "abc", "-5", "", True])
def test_to_cents_rejects_bad_amounts(bad):
    with pytest.raises(InvoiceError):
        to_cents(bad)


def test_line_items_total_is_recomputed_server_side():
    items, total = normalize_line_items(
        [{"name": "Setup", "quantity": 2, "unit_amount_cents": 10000},
         {"name": "Support", "unit_amount": "25.50"}], max_total_cents=1_000_000)
    assert total == 2 * 10000 + 2550
    assert items[1]["quantity"] == 1


def test_line_items_limits():
    with pytest.raises(InvoiceError):
        normalize_line_items([], max_total_cents=100000)
    with pytest.raises(InvoiceError):
        normalize_line_items([{"name": "x", "unit_amount_cents": 500000}], max_total_cents=100000)
    with pytest.raises(InvoiceError):
        normalize_line_items([{"name": "", "unit_amount_cents": 500}], max_total_cents=100000)


def test_currency_allowlist():
    assert validate_currency("usd", ("USD",)) == "USD"
    with pytest.raises(InvoiceError):
        validate_currency("PKR", ("USD", "EUR"))


def test_terms_hash_changes_when_anything_changes():
    items = [{"name": "A", "description": "", "quantity": 1, "unit_amount_cents": 1000}]
    h = terms_hash("s", "b", items, "USD")
    assert h == terms_hash("s", "b", items, "USD")
    assert h != terms_hash("s", "b!", items, "USD")
    assert h != terms_hash("s", "b", [{**items[0], "unit_amount_cents": 1001}], "USD")


def test_invoice_payload_shape():
    items, _ = normalize_line_items([{"name": "Website", "unit_amount_cents": 25000}], max_total_cents=10**7)
    p = build_invoice_payload(invoice_number="QLF-1", currency="USD", line_items=items,
                              recipient_company="Acme", recipient_email="a@acme.com", today=date(2026, 11, 10))
    assert p["detail"]["currency_code"] == "USD"
    assert p["detail"]["payment_term"]["due_date"] == "2026-11-24"
    assert p["items"][0]["unit_amount"] == {"currency_code": "USD", "value": "250.00"}
    assert p["primary_recipients"][0]["billing_info"]["email_address"] == "a@acme.com"
    with pytest.raises(InvoiceError):
        build_invoice_payload(invoice_number="x", currency="USD", line_items=items,
                              recipient_company="Acme", recipient_email="")


def test_paypal_link_parsing():
    assert invoice_id_from_href("https://api-m.sandbox.paypal.com/v2/invoicing/invoices/INV2-AB12") == "INV2-AB12"
    assert payer_link({"detail": {"metadata": {"recipient_view_url": "https://pay/x"}}}) == "https://pay/x"
    assert payer_link({"links": [{"rel": "recipient-view", "href": "https://pay/y"}]}) == "https://pay/y"
    assert payer_link({}) is None


# ---------------------------------------------------------------- config safety
def test_live_mode_needs_double_opt_in(monkeypatch):
    for k in ("GTM_PAYPAL_ENV", "GTM_PAYPAL_ALLOW_LIVE", "PAYPAL_ENV"):
        monkeypatch.delenv(k, raising=False)
    assert load_config().is_sandbox
    monkeypatch.setenv("GTM_PAYPAL_ENV", "live")
    assert load_config().is_sandbox  # still sandbox without the second flag
    monkeypatch.setenv("GTM_PAYPAL_ALLOW_LIVE", "1")
    assert not load_config().is_sandbox


def test_public_status_never_leaks_secrets():
    cfg = PayPalConfig("id-123", "secret-xyz", "WH-1", "sandbox", ("USD",), 100000, "")
    blob = json.dumps(cfg.public_status())
    assert "secret-xyz" not in blob and "id-123" not in blob and "WH-1" not in blob


# ---------------------------------------------------------------- webhook signature (fail closed)
class _Resp:
    def __init__(self, status, data):
        self.status_code, self._d, self.content, self.headers, self.reason_phrase = status, data, b"x", {}, ""

    def json(self):
        return self._d


class _Http:
    def __init__(self, verdict="SUCCESS"):
        self.verdict, self.calls = verdict, []

    def post(self, url, headers=None, content=None):
        self.calls.append((url, content))
        if url.endswith("/v1/oauth2/token"):
            return _Resp(200, {"access_token": "t", "expires_in": 3000})
        return _Resp(200, {"verification_status": self.verdict})


_HDRS = {"PAYPAL-AUTH-ALGO": "SHA256withRSA", "PAYPAL-CERT-URL": "https://x/c.pem",
         "PAYPAL-TRANSMISSION-ID": "t1", "PAYPAL-TRANSMISSION-SIG": "sig", "PAYPAL-TRANSMISSION-TIME": "2026-11-10T00:00:00Z"}


def _client(http, webhook_id="WH-1"):
    return PayPalClient(PayPalConfig("cid-test-1", "sec", webhook_id, "sandbox", ("USD",), 100000, ""), http=http)


def test_webhook_verification_success_splices_raw_body():
    http = _Http("SUCCESS")
    raw = b'{"id":"WH-EVT","event_type":"INVOICING.INVOICE.PAID"}'
    assert _client(http).verify_webhook_signature(_HDRS, raw)
    sent = http.calls[-1][1]
    assert raw.decode() in sent and '"webhook_id":"WH-1"' in sent
    json.loads(sent)  # still valid JSON


def test_webhook_verification_fails_closed():
    raw = b"{}"
    assert not _client(_Http("FAILURE")).verify_webhook_signature(_HDRS, raw)
    assert not _client(_Http("SUCCESS"), webhook_id="").verify_webhook_signature(_HDRS, raw)
    missing = {k: v for k, v in _HDRS.items() if k != "PAYPAL-TRANSMISSION-SIG"}
    assert not _client(_Http("SUCCESS")).verify_webhook_signature(missing, raw)


# ---------------------------------------------------------------- webhook processing (stubbed store)
class _FakeStore:
    def __init__(self, status="SENT"):
        self.events, self.status, self.updates = set(), status, []

    def record_event(self, db, eid, et, pid, raw):
        if eid in self.events:
            return False
        self.events.add(eid)
        return True

    def get_invoice_by_paypal_id(self, db, pid):
        return {"invoice_id": "inv_1", "lead_id": "L1", "status": self.status} if pid == "INV2-1" else None

    def set_invoice_status(self, db, iid, status, when):
        self.status = status
        self.updates.append(status)


class _Db:
    def add_event(self, *a, **k):
        pass


def _evt(eid, etype, inv="INV2-1"):
    return {"id": eid, "event_type": etype, "create_time": "2026-11-10T00:00:00Z", "resource": {"id": inv}}


def test_webhook_paid_then_duplicate_then_stale(monkeypatch):
    fs = _FakeStore("SENT")
    monkeypatch.setattr(webhooks, "store", fs)
    assert webhooks.process_event(_Db(), _evt("E1", "INVOICING.INVOICE.PAID"), "{}")["status"] == "PAID"
    assert webhooks.process_event(_Db(), _evt("E1", "INVOICING.INVOICE.PAID"), "{}")["duplicate"]
    # a late SENT / CANCELLED event must never undo a payment
    assert webhooks.process_event(_Db(), _evt("E2", "INVOICING.INVOICE.SENT"), "{}")["stale"]
    assert webhooks.process_event(_Db(), _evt("E3", "INVOICING.INVOICE.CANCELLED"), "{}")["stale"]
    assert fs.updates == ["PAID"]


def test_webhook_unknown_invoice_and_nested_resource(monkeypatch):
    fs = _FakeStore("SENT")
    monkeypatch.setattr(webhooks, "store", fs)
    assert webhooks.process_event(_Db(), _evt("E9", "INVOICING.INVOICE.PAID", "INV2-OTHER"), "{}")["unmatched"]
    nested = {"id": "E10", "event_type": "INVOICING.INVOICE.PAID", "resource": {"invoice": {"id": "INV2-1"}}}
    assert webhooks.process_event(_Db(), nested, "{}")["status"] == "PAID"
    assert webhooks.process_event(_Db(), {"id": "E11", "event_type": "SOMETHING.ELSE", "resource": {}}, "{}")["ignored"]


# ---------------------------------------------------------------- calibration
def _synthetic_outcomes(n=120):
    return [calibration.Outcome(r["status"] == "PAID", r["score"], r["breakdown"]) for r in seed.generate(n)]


def test_calibration_needs_enough_data():
    res = calibration.analyse(_synthetic_outcomes(10))
    assert res["status"] == "insufficient_data"
    assert res["proposed_weights"] == res["current_weights"]


def test_calibration_proposes_bounded_balanced_weights():
    res = calibration.analyse(_synthetic_outcomes(150))
    assert res["status"] == "proposed"
    cur, new = res["current_weights"], res["proposed_weights"]
    assert sum(new.values()) == sum(cur.values()) == 100
    assert all(abs(new[k] - cur[k]) <= calibration.MAX_STEP_POINTS for k in cur)
    # the seeder hides a stronger effect in online_gap / pain_evidence: they should gain weight
    assert new["online_gap"] > cur["online_gap"] or new["pain_evidence"] > cur["pain_evidence"]
    assert "Nothing changes until you accept" in res["explanation"]


def test_calibration_is_deterministic_and_does_not_mutate_inputs():
    outs = _synthetic_outcomes(80)
    w = dict(calibration.DEFAULT_WEIGHTS)
    a = calibration.analyse(outs, w)
    assert w == calibration.DEFAULT_WEIGHTS
    assert a == calibration.analyse(outs, w)


def test_calibration_all_same_outcome_changes_nothing():
    outs = [calibration.Outcome(False, 60, {"online_gap": 20}) for _ in range(30)]
    assert calibration.analyse(outs)["status"] == "no_change"


def test_small_buckets_are_ignored():
    # only 3 leads are "strong" on online_gap -> below MIN_BUCKET -> multiplier stays 1.0
    outs = [calibration.Outcome(i % 2 == 0, 50, {"online_gap": 25 if i < 3 else 0}) for i in range(30)]
    stats = {s["dimension"]: s for s in calibration.analyse(outs)["dimension_stats"]}
    assert not stats["online_gap"]["eligible"] and stats["online_gap"]["multiplier"] == 1.0


def test_smoothing_shrinks_toward_prior():
    assert calibration.smoothed_rate(3, 3, 0.2) < 0.7  # 3/3 paid is not trusted as 100%
    assert abs(calibration.smoothed_rate(0, 0, 0.2) - 0.2) < 1e-9


def test_score_band_rates_cover_all_scores():
    bands = calibration.score_band_rates(_synthetic_outcomes(100))
    assert sum(b["n"] for b in bands) == 100


# ---------------------------------------------------------------- proposal agent guardrails
LEAD = {"company_name": "Bright Mart", "contact_name": "Sara Khan", "pain_signal": "customers ask for home delivery",
        "online_presence": {"pain_from_reviews": ["slow phone ordering"], "notes": []}}


def test_deterministic_proposal_uses_base_price_and_range():
    d = proposal.deterministic_proposal(LEAD, "online ordering website", "Acme Studio", 50000, "USD")
    assert d.amount_cents == 50000 and d.source == "template"
    assert (d.range_min_cents, d.range_max_cents) == (40000, 62500)
    assert "Sara" in d.body and "customers ask for home delivery" in d.body


def test_ungrounded_numbers_are_rejected():
    facts = proposal.gather_facts(LEAD)
    assert proposal.sentence_is_grounded("Customers ask for home delivery.", facts)
    assert not proposal.sentence_is_grounded("You could grow sales by 40% with delivery.", facts)


class _LLM:
    name = "fake"

    def __init__(self, text):
        self.text = text

    async def complete(self, system, user, *, max_tokens=400):
        return self.text


def test_llm_sentence_is_used_only_when_grounded():
    good = asyncio.run(proposal.draft_proposal(_LLM("Home delivery requests suggest an ordering site would help."),
                                               LEAD, "ordering site", "Acme", 50000, "USD"))
    assert good.source == "llm" and "ordering site would help" in good.body
    bad = asyncio.run(proposal.draft_proposal(_LLM("We will boost revenue 300% in 30 days."),
                                              LEAD, "ordering site", "Acme", 50000, "USD"))
    assert bad.source == "template" and "300%" not in bad.body
    assert asyncio.run(proposal.draft_proposal(None, LEAD, "x", "Acme", 50000, "USD")).source == "template"


# ---------------------------------------------------------------- ledger snapshot feeds calibration
def test_score_snapshot_uses_breakdown_keys_that_calibration_reads():
    from types import SimpleNamespace
    from gtm_engine.paypal import service
    lead = SimpleNamespace(total_score=72, evidence={"score": {
        "review_band": 20, "rating_score": 8, "proximity_tier": 12, "online_gap": 22, "pain_evidence": 10}})
    total, snap = service.score_snapshot(lead)
    assert total == 72 and snap["rating_score"] == 8 and snap["online_gap"] == 22
    outs = [calibration.Outcome(i % 3 == 0, total, snap) for i in range(30)]
    stats = {s["dimension"]: s for s in calibration.analyse(outs)["dimension_stats"]}
    assert stats["rating"]["n_high"] == 30  # 8/10 points is "strong"; proves the key matched
