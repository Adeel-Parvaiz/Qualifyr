"""Phase B: phone type, decision-maker email discovery, verification gate, provenance."""

import httpx
import pytest
import respx

from conftest import fixture
from gtm_engine.discovery.geocode import NOMINATIM_URL
from gtm_engine.enrichment.email_patterns import candidates, discover, infer_pattern, name_parts
from gtm_engine.enrichment.phones import best_phone, classify_phone
from gtm_engine.models import DiscoveredCompany, EmailStatus
from gtm_engine.pipeline import Pipeline
from gtm_engine.scraping.fetcher import HttpFetcher
from gtm_engine.storage.database import Database
from gtm_engine.validation.verifier import MxOnlyVerifier, VerifyResult, VerifyStatus, build_verifier


# --- phones -----------------------------------------------------------------------------------

@pytest.mark.parametrize("raw,kind,e164", [
    ("0335 5765923", "mobile", "+923355765923"),
    ("+92 300 1234567", "mobile", "+923001234567"),
    ("0092-321-9876543", "mobile", "+923219876543"),
    ("051-2345678", "landline", "+92512345678"),
    ("+92 42 35761234", "landline", "+924235761234"),
    ("021 34567890", "landline", "+922134567890"),
    ("12345", "unknown", None),
])
def test_classify_phone(raw, kind, e164):
    info = classify_phone(raw)
    assert info.kind == kind and info.e164 == e164


def test_best_phone_prefers_mobile():
    assert best_phone(["051-2345678", "0300 1234567"]).kind == "mobile"
    assert best_phone(["051-2345678"]).kind == "landline"
    assert best_phone([]) is None


# --- patterns ---------------------------------------------------------------------------------

def test_name_parts_drops_honorifics():
    assert name_parts("Dr. Muhammad Usman Khan").first == "usman"
    assert name_parts("Muhammad Ali").first == "muhammad"  # two tokens: keep both
    assert name_parts("Ahmed Raza").last == "raza"
    assert name_parts("Ahmed") is None


def test_candidates_order_and_known_pattern(defaults):
    c = candidates("Ahmed Raza", "zarafabrics.pk", generic_prefixes=defaults.generic_email_prefixes)
    assert c[0] == ("ahmed.raza@zarafabrics.pk", "first.last")
    assert ("araza@zarafabrics.pk", "flast") in c
    c2 = candidates("Ahmed Raza", "zarafabrics.pk", known_pattern="flast")
    assert c2[0] == ("araza@zarafabrics.pk", "flast")


def test_infer_pattern_from_public_address():
    assert infer_pattern("sana.malik@zarafabrics.pk", "Sana Malik") == "first.last"
    assert infer_pattern("smalik@zarafabrics.pk", "Sana Malik") == "flast"
    assert infer_pattern("info@zarafabrics.pk", "Sana Malik") is None


class ScriptedVerifier:
    """Answers from a table; anything else is invalid. `catch_all` marks domains."""

    name = "scripted"

    def __init__(self, deliverable: set[str] = (), catch_all: set[str] = ()):
        self.deliverable, self.catch_all_domains = set(deliverable), set(catch_all)
        self.calls: list[str] = []

    async def verify(self, email: str) -> VerifyResult:
        self.calls.append(email)
        if email in self.deliverable:
            return VerifyResult(VerifyStatus.DELIVERABLE, self.name, "250 ok")
        return VerifyResult(VerifyStatus.INVALID, self.name, "550 no such user")

    async def is_catch_all(self, domain: str) -> bool | None:
        return domain in self.catch_all_domains


async def test_discover_confirms_only_deliverable(defaults):
    v = ScriptedVerifier(deliverable={"araza@zarafabrics.pk"})
    found = await discover("Ahmed Raza", "zarafabrics.pk", v, generic_prefixes=defaults.generic_email_prefixes)
    assert found.status == VerifyStatus.DELIVERABLE and found.email == "araza@zarafabrics.pk" and found.pattern == "flast"
    assert v.calls[:4] == ["ahmed.raza@zarafabrics.pk", "ahmed@zarafabrics.pk", "ahmedraza@zarafabrics.pk", "araza@zarafabrics.pk"]


async def test_discover_gives_up_when_nothing_confirms(defaults):
    found = await discover("Ahmed Raza", "zarafabrics.pk", ScriptedVerifier(), generic_prefixes=defaults.generic_email_prefixes)
    assert found.status == VerifyStatus.INVALID and found.email is None and len(found.tried) == 5


async def test_discover_catch_all_is_risky_not_accepted(defaults):
    v = ScriptedVerifier(deliverable={"ahmed.raza@zarafabrics.pk"}, catch_all={"zarafabrics.pk"})
    found = await discover("Ahmed Raza", "zarafabrics.pk", v)
    assert found.status == VerifyStatus.RISKY and found.email == "ahmed.raza@zarafabrics.pk"
    assert v.calls == []  # not even tried: a catch-all cannot confirm anything


async def test_discover_without_verifier_is_candidate_only(defaults):
    found = await discover("Ahmed Raza", "zarafabrics.pk", MxOnlyVerifier())
    assert found.status == VerifyStatus.UNVERIFIED and found.email == "ahmed.raza@zarafabrics.pk"
    assert "not sent" in found.reason


async def test_build_verifier_off_and_auto_fallback(monkeypatch):
    assert (await build_verifier("off")).name == "mx_only"
    monkeypatch.delenv("GTM_REACHER_URL", raising=False)
    monkeypatch.setattr("gtm_engine.validation.verifier.port25_reachable", lambda *a, **k: _false())
    assert (await build_verifier("auto")).name == "mx_only"
    assert (await build_verifier("direct")).name == "mx_only"   # direct degrades when port 25 is blocked
    monkeypatch.setenv("GTM_REACHER_URL", "http://reacher.local")
    assert (await build_verifier("auto")).name == "reacher"


async def _false():
    return False


# --- pipeline: named CEO with only info@ gets a confirmed personal address --------------------

class FakeMX:
    async def has_mx(self, domain: str) -> bool:
        return True


def _html(body: str) -> httpx.Response:
    return httpx.Response(200, text=body, headers={"content-type": "text/html; charset=utf-8"})


@respx.mock
async def test_pipeline_discovers_decision_maker_email(campaign, settings, defaults):
    about = fixture("retailer_about.html")
    contact = fixture("retailer_contact.html").replace("ahmed.raza@zarafabrics.pk", "orders@zarafabrics.pk")
    respx.get(url__startswith=NOMINATIM_URL).mock(return_value=httpx.Response(200, json=[{"boundingbox": ["33.5", "33.8", "72.8", "73.2"]}]))
    respx.get("https://www.zarafabrics.pk/").mock(return_value=_html(fixture("retailer_home.html")))
    respx.get("https://www.zarafabrics.pk/pages/about-us").mock(return_value=_html(about))
    respx.get("https://www.zarafabrics.pk/pages/contact-us").mock(return_value=_html(contact))
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(404))
    campaign.osm_categories = []
    campaign.geography.cities = ["Islamabad"]
    async def fake_discover(_c, _p=None):
        return [DiscoveredCompany(name="Zara Fabrics", website="https://www.zarafabrics.pk", city="Islamabad",
                                  country="Pakistan", source="osm", phone="0300 1234567")]

    verifier = ScriptedVerifier(deliverable={"ahmed.raza@zarafabrics.pk"})
    db = Database(settings.database_url)
    async with HttpFetcher(settings) as fetcher:
        pipeline = Pipeline(settings, defaults, db, fetcher, mx=FakeMX(), verifier=verifier)
        pipeline.discover = fake_discover
        result = await pipeline.run(campaign)
    lead = result.leads[0]
    assert lead.contact_name == "Ahmed Raza"
    assert lead.contact_email == "ahmed.raza@zarafabrics.pk" and lead.email_status == EmailStatus.DELIVERABLE
    assert lead.outreach_ready is True
    assert lead.provenance["contact_email"].startswith("pattern first.last, confirmed by scripted")
    assert "email_discovery" in lead.provenance and "contact_name" in lead.provenance
    assert lead.phone_type in ("mobile", "landline")
    assert "decision-maker" in lead.score_reason and "email confirmed" in lead.score_reason
    db.close()


@respx.mock
async def test_pipeline_keeps_generic_when_unconfirmed(campaign, settings, defaults):
    about = fixture("retailer_about.html")
    contact = fixture("retailer_contact.html").replace("ahmed.raza@zarafabrics.pk", "orders@zarafabrics.pk")
    respx.get(url__startswith=NOMINATIM_URL).mock(return_value=httpx.Response(200, json=[{"boundingbox": ["33.5", "33.8", "72.8", "73.2"]}]))
    respx.get("https://www.zarafabrics.pk/").mock(return_value=_html(fixture("retailer_home.html")))
    respx.get("https://www.zarafabrics.pk/pages/about-us").mock(return_value=_html(about))
    respx.get("https://www.zarafabrics.pk/pages/contact-us").mock(return_value=_html(contact))
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(404))
    campaign.osm_categories = []
    campaign.geography.cities = ["Islamabad"]

    async def fake_discover(_c, _p=None):
        return [DiscoveredCompany(name="Zara Fabrics", website="https://www.zarafabrics.pk", city="Islamabad", country="Pakistan", source="osm")]

    db = Database(settings.database_url)
    async with HttpFetcher(settings) as fetcher:
        pipeline = Pipeline(settings, defaults, db, fetcher, mx=FakeMX(), verifier=MxOnlyVerifier())
        pipeline.discover = fake_discover
        result = await pipeline.run(campaign)
    lead = result.leads[0]
    assert lead.contact_email == "info@zarafabrics.pk" and lead.email_status == EmailStatus.GENERIC
    assert lead.candidate_email == "ahmed.raza@zarafabrics.pk"   # shown to the reviewer, never sent
    assert "no verifier available" in lead.provenance["email_discovery"]
    db.close()


# HunterVerifier was removed on 2026-10-09; its repoll test went with it. The engine's
# default verifier is now DirectSmtpVerifier (own-infra SMTP RCPT), covered elsewhere in
# this file via ScriptedVerifier and the pipeline integration tests above.
