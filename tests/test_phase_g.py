"""Phase G: intent/requirement scraping, LLM layer guards, reviewer-accuracy metric."""

import httpx
import pytest
import respx

from conftest import bypass_auth
from gtm_engine.config.loader import load_defaults
from gtm_engine.intent.company_pages import intent_from_pages
from gtm_engine.intent.ppra import PPRA_URL, PPRATenders, clean_org, matches_campaign, parse_ppra
from gtm_engine.llm.client import grounded, keep_grounded, parse_json_object
from gtm_engine.llm.tasks import classify_reply, draft_hook, extract_requirement
from gtm_engine.models import CompanyType, EmailStatus, Lead, Priority, Signals
from gtm_engine.pipeline import build_personalization_hook
from gtm_engine.models import DiscoveredCompany
from gtm_engine.scraping.fetcher import HttpFetcher
from gtm_engine.scraping.parsers import parse_page
from gtm_engine.scraping.site_crawler import SiteSnapshot
from gtm_engine.storage.database import Database

PPRA_HTML = """<table>
<tr><th>Sr</th><th>Tender No</th><th>Tender Details</th><th>Organization Details</th><th>Status</th><th>Advertised</th><th>Closing</th><th>Actions</th></tr>
<tr><td>1</td><td>TS0001E</td><td>Supply of readymade garments and uniforms for staff</td><td>Pakistan Railways Lahore</td><td>Published</td><td>Sep 21, 2099</td><td>Oct 10, 2099 10:30 AM</td><td><a href="https://epms.ppra.gov.pk/t/1">view</a></td></tr>
<tr><td>2</td><td>TS0002E</td><td>Procurement of inventory management software (ERP) for stores</td><td>Utility Stores Corporation</td><td>Published</td><td>Sep 20, 2099</td><td>Nov 01, 2099 11:00 AM</td><td></td></tr>
<tr><td>3</td><td>TS0003E</td><td>Tank spares</td><td>Directorate General Procurement (Army)</td><td>Published</td><td>Sep 21, 2099</td><td>Dec 07, 2099 10:30 AM</td><td></td></tr>
</table>"""


def test_parse_ppra_rows_and_dates():
    rows = parse_ppra(PPRA_HTML)
    assert len(rows) == 3
    assert rows[0]["tender_no"] == "TS0001E" and rows[0]["organization"] == "Pakistan Railways Lahore"
    assert rows[0]["advertised"] == "2099-09-21" and rows[0]["closing"] == "2099-10-10"
    assert rows[0]["url"] == "https://epms.ppra.gov.pk/t/1" and rows[1]["url"] == PPRA_URL


def test_matches_campaign_terms(campaign):
    campaign.intent_keywords = ["inventory", "erp", "uniforms"]
    rows = parse_ppra(PPRA_HTML)
    assert matches_campaign(rows[0], campaign) == ["uniforms"]
    assert set(matches_campaign(rows[1], campaign)) == {"erp", "inventory"}
    assert matches_campaign(rows[2], campaign) == []


@respx.mock
async def test_ppra_discovery_and_matching(campaign, settings):
    respx.get(PPRA_URL).mock(return_value=httpx.Response(200, text=PPRA_HTML, headers={"content-type": "text/html"}))
    campaign.intent_keywords = ["inventory", "erp", "uniforms"]
    async with HttpFetcher(settings) as fetcher:
        src = PPRATenders(fetcher, settings)
        found = [c async for c in src.discover(campaign)]
        sigs = await src.signals_for("Utility Stores", campaign)
    assert [c.name for c in found] == ["Pakistan Railways Lahore", "Utility Stores Corporation"]
    assert found[1].extra["intent"]["kind"] == "tender" and found[1].extra["intent"]["deadline"] == "2099-11-01"
    assert len(sigs) == 1 and sigs[0].organization == "Utility Stores Corporation" and "erp" in sigs[0].matched_terms


def test_intent_from_company_pages():
    defaults = load_defaults()
    snap = SiteSnapshot(website="https://x.pk", final_url="https://x.pk/", reachable=True, https=True)
    snap.pages["home"] = parse_page("https://x.pk/", "<html><body><p>Welcome. We invite suppliers: Request for Quotation for packaging material, deadline Friday.</p></body></html>")
    snap.pages["careers"] = parse_page("https://x.pk/careers", "<html><body><h2>Open roles</h2><p>Procurement Manager - Lahore. Sales Executive.</p></body></html>")
    sigs = intent_from_pages(snap, defaults)
    kinds = {(s.kind, s.matched_terms[0]) for s in sigs}
    assert ("rfq", "request for quotation") in kinds and ("hiring", "procurement manager") in kinds
    assert all(s.source_url for s in sigs)


# --- LLM guards ---------------------------------------------------------------------------

class FakeLLM:
    name = "fake"

    def __init__(self, reply: str):
        self.reply = reply
        self.calls = 0

    async def complete(self, system, user, *, max_tokens=400):
        self.calls += 1
        return self.reply


def test_grounding_helpers():
    src = "Procurement of inventory management software (ERP) for 120 stores, closing Nov 01 2026"
    assert grounded("inventory management software", src) and grounded("120 stores", src)
    assert not grounded("500 stores", src) and not grounded("Karachi", src)
    assert keep_grounded({"need": "inventory management software", "quantity": "120 stores", "budget": "PKR 5m"}, src,
                         ("need", "quantity", "budget")) == {"need": "inventory management software", "quantity": "120 stores"}
    assert parse_json_object('Sure! {"a": 1}') == {"a": 1} and parse_json_object("nope") is None


async def test_extract_requirement_drops_hallucinations():
    src = "TS0002E: Procurement of inventory management software (ERP) for stores"
    llm = FakeLLM('{"need": "inventory management software (ERP)", "quantity": "350 licences", "deadline": null, "location": "Islamabad", "budget": null}')
    out = await extract_requirement(llm, src)
    assert out == {"need": "inventory management software (ERP)", "by": "llm:fake"}   # invented fields removed
    assert await extract_requirement(None, src) is None
    assert await extract_requirement(FakeLLM("garbage"), src) is None


async def test_reply_second_opinion_and_hook_guard():
    assert await classify_reply(FakeLLM("interested"), "Re:", "Haan bhai, call kar lo kal") == "interested"
    assert await classify_reply(FakeLLM("maybe"), "Re:", "…") is None
    assert await classify_reply(None, "Re:", "x") is None
    hook = await draft_hook(FakeLLM("I noticed you sell online and run several outlets in Lahore."), "Zara", ["sells online", "operates multiple outlets"])
    assert hook and "online" in hook
    assert await draft_hook(FakeLLM("Congratulations on your massive growth and award-winning team!"), "Zara", ["sells online"]) is None  # no fact keyword -> rejected


def test_hook_mentions_intent_first():
    sig = Signals(intent=[{"kind": "tender", "text": "TS0002E: Procurement of inventory management software", "matched_terms": ["erp"]}])
    hook = build_personalization_hook(DiscoveredCompany(name="USC", city="Islamabad", source="ppra"), None, sig)
    assert "tendering: TS0002E" in hook


# --- reviewer metric ------------------------------------------------------------------------

@pytest.fixture
def client(settings, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import gtm_engine.api.main as m
    monkeypatch.setattr(m, "_settings", settings)
    camp_dir = tmp_path / "campaigns"
    camp_dir.mkdir()
    (camp_dir / "t.yaml").write_text("campaign_id: test-retail\nname: T\noffer: x\ngeography:\n  cities: [Lahore]\nosm_categories: [shop=clothes]\n", encoding="utf-8")
    monkeypatch.setattr(m, "CAMPAIGN_DIR", camp_dir)
    db = Database(settings.database_url)
    for i in range(4):
        db.save_lead(Lead(campaign_id="test-retail", company_name=f"Co {i}", domain=f"co{i}.pk", company_type=CompanyType.BUYER,
                          total_score=80, priority=Priority.HIGH, contact_email=f"a@co{i}.pk", email_status=EmailStatus.MX_VALID,
                          outreach_ready=True, intent_signals=[{"kind": "tender", "text": "x"}] if i == 0 else []), "r", f"co{i}.pk")
    ids = [l.lead_id for l in db.list_leads("test-retail")]
    db.close()
    bypass_auth(m, monkeypatch)
    return TestClient(m.app), ids


def test_review_verdicts_drive_accuracy(client):
    c, ids = client
    s0 = c.get("/campaigns/test-retail/stats").json()
    assert s0["accuracy"] is None and s0["reviewed"] == 0 and s0["with_intent"] == 1
    assert c.post(f"/leads/{ids[0]}/review", json={"verdict": "correct"}).json()["review_verdict"] == "correct"
    c.post(f"/leads/{ids[1]}/review", json={"verdict": "correct"})
    c.post(f"/leads/{ids[2]}/review", json={"verdict": "wrong_company"})
    assert c.post(f"/leads/{ids[3]}/review", json={"verdict": "nonsense"}).status_code == 422
    s1 = c.get("/campaigns/test-retail/stats").json()
    assert (s1["reviewed"], s1["correct"], s1["accuracy"]) == (3, 2, 0.667)
    assert s1["verdicts"]["wrong_company"] == 1
    wrong = c.get(f"/leads/{ids[2]}").json()
    assert wrong["outreach_ready"] is False   # a wrong company never gets emailed
    assert c.post(f"/leads/{ids[2]}/review", json={"verdict": "clear"}).json()["review_verdict"] is None
    assert c.get("/campaigns/test-retail/stats").json()["reviewed"] == 2


def test_outreach_activity_endpoint(client, settings):
    c, ids = client
    # `settings` here is the same instance the client fixture built on, so this Database
    # points at the same throwaway schema the API routes read from.
    db = Database(settings.database_url)
    db.add_event(ids[0], "sent", step="1", detail="first touch")
    db.add_event(ids[0], "opened", step="1")
    db.add_event(ids[1], "sent", step="1")
    db.close()

    r = c.get("/campaigns/test-retail/outreach/activity")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 3
    # newest first (event_id DESC): Co 1's "sent" was inserted last, Co 0's two come after.
    assert [row["event_type"] for row in rows] == ["sent", "opened", "sent"]
    assert rows[0]["lead_id"] == ids[1]
    assert rows[0]["company_name"] == "Co 1" and rows[0]["contact_email"] == "a@co1.pk"
    assert rows[1]["lead_id"] == ids[0] and rows[1]["company_name"] == "Co 0"
    assert rows[2]["lead_id"] == ids[0] and rows[2]["contact_email"] == "a@co0.pk"
    assert "data_json" not in rows[0]

    assert c.get("/campaigns/test-retail/outreach/activity?limit=1").json() == rows[:1]


def test_clean_org_names():
    assert clean_org("Pakistan State Oil (PSO) PSO Karachi") == "Pakistan State Oil"
    assert clean_org("State Bank of Pakistan (SBP) State Bank of Pakistan Karachi") == "State Bank of Pakistan"
    assert clean_org("Pakistan Railways Lahore Pakistan Railways Lahore") == "Pakistan Railways Lahore"
    assert clean_org("Ministry of Energy (Power Division)") == "Ministry of Energy (Power Division)"  # a qualifier, not an abbreviation
    assert clean_org("Utility Stores Corporation") == "Utility Stores Corporation"


def test_closed_tenders_are_not_intent():
    from gtm_engine.intent.ppra import is_open
    assert is_open({"closing": "2026-10-10"}, today="2026-09-21")
    assert not is_open({"closing": "2026-09-15"}, today="2026-09-21")
    assert is_open({"closing": None}, today="2026-09-21")


def test_live_tender_is_buyer_evidence(campaign):
    from gtm_engine.qualification.buyer_classifier import BuyerClassifier, TextBundle
    b = TextBundle(name="Utility Stores Corporation", title=None, description=None, about_text=None, body_text="",
                   category="tender=ppra", site_reachable=False, tender_terms=["inventory", "erp"])
    cls = BuyerClassifier(campaign, load_defaults()).classify(b)
    assert cls.company_type == CompanyType.BUYER and cls.confidence == 0.9 and "live tender" in cls.reasons[0]
