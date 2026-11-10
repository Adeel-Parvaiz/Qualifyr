"""Phase E: rule-based reply classification and the actions it drives."""

from datetime import datetime, timezone

import pytest

from gtm_engine.models import CompanyType, EmailStatus, Lead, Priority, SequenceStatus
from gtm_engine.outreach.config import OutreachSettings, load_templates
from gtm_engine.outreach.ledger import Ledger
from gtm_engine.outreach.reply_classifier import classify, parse_return_date, strip_quoted
from gtm_engine.outreach.reply_state import InboundMessage, apply_inbound
from gtm_engine.outreach.sequencer import due_leads, enqueue, send_due
from gtm_engine.storage.database import Database
from test_outreach import MON_10AM_PKT, FakeSender

NOW = datetime(2027, 3, 4, 10, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("subject,body,label", [
    ("Re: Zara", "Sure, let's talk Thursday.", "interested"),
    ("Re: Zara", "Please send me the pricing and a deck.", "interested"),
    ("Re: Zara", "Not interested, thanks.", "not_interested"),
    ("Re: Zara", "We already have a system for this.", "not_interested"),
    ("Automatic reply: Re: Zara", "I am out of the office until 12 March and will have limited access to email.", "out_of_office"),
    ("Re: Zara", "I'm not the right person for this. Please contact Sana Malik (sana.malik@zarafabrics.pk).", "wrong_person"),
    ("Re: Zara", "Ahmed has left the company; Bilal Khan looks after operations now: bilal@zarafabrics.pk", "wrong_person"),
    ("Re: Zara", "Please remove me from your list.", "unsubscribe"),
    ("STOP", "", "unsubscribe"),
    ("Thank you for contacting Zara Fabrics", "We have received your message and will get back to you shortly. Ticket #4821", "auto_reply"),
    ("Re: Zara", "Can you clarify what exactly you do?", "reply"),
])
def test_classify_labels(subject, body, label):
    assert classify(subject, body, own_email="me@gmail.com", sender="ahmed@zarafabrics.pk", now=NOW).label == label


def test_quoted_history_is_ignored():
    body = "Not the right person.\n\nOn Mon, 1 Mar 2027 Qualifyr Team <me@gmail.com> wrote:\n> I'm interested in helping you scale... let's talk"
    c = classify("Re:", body, own_email="me@gmail.com", sender="a@x.pk", now=NOW)
    assert c.label == "wrong_person"
    assert "interested" not in strip_quoted(body).lower()


def test_referral_extraction():
    c = classify("Re:", "Please contact Sana Malik (sana.malik@zarafabrics.pk), she handles this.",
                 own_email="me@gmail.com", sender="ahmed@zarafabrics.pk", now=NOW)
    assert c.referred_email == "sana.malik@zarafabrics.pk" and c.referred_name == "Sana Malik"
    c2 = classify("Re:", "Reply to me@gmail.com or ahmed@zarafabrics.pk", own_email="me@gmail.com", sender="ahmed@zarafabrics.pk", now=NOW)
    assert c2.referred_email is None


def test_return_date_parsing():
    assert parse_return_date("I will be back on 12 March", NOW) == datetime(2027, 3, 12, tzinfo=timezone.utc)
    assert parse_return_date("returning March 15th, 2027", NOW) == datetime(2027, 3, 15, tzinfo=timezone.utc)
    assert parse_return_date("back on 20/03", NOW) == datetime(2027, 3, 20, tzinfo=timezone.utc)
    assert parse_return_date("out until 3 Jan", datetime(2027, 12, 20, tzinfo=timezone.utc)) == datetime(2028, 1, 3, tzinfo=timezone.utc)
    assert parse_return_date("no date here", NOW) is None


# --- actions on the sequence ------------------------------------------------------------

def _lead(name, email):
    return Lead(campaign_id="test-retail", company_name=name, domain=email.split("@")[1], city="Islamabad",
                company_type=CompanyType.BUYER, total_score=90, priority=Priority.HIGH, contact_email=email,
                contact_name="Ahmed Raza", email_status=EmailStatus.MX_VALID, outreach_ready=True)


@pytest.fixture
def world(settings, campaign, tmp_path):
    db = Database(settings.database_url)
    db.upsert_campaign(campaign.campaign_id, campaign.name, campaign.model_dump(mode="json"))
    for l in (_lead("Zara Fabrics", "ahmed@zarafabrics.pk"), _lead("MCC", "info@mcc.com.pk"), _lead("Vegas", "x@vegas.pk")):
        db.save_lead(l, "run", l.domain)
    s = OutreachSettings(daily_limit=10, warmup_enabled=False, jitter_max_s=0, require_approval=False)
    ledger = Ledger(tmp_path / "l.json")
    enqueue(db, "test-retail", s, ledger, now=MON_10AM_PKT)
    send_due(db, campaign, s, load_templates(), FakeSender(), ledger, now=MON_10AM_PKT, sleep=lambda x: None)
    yield db, s, ledger, campaign
    db.close()


def test_out_of_office_postpones_instead_of_stopping(world):
    db, s, ledger, campaign = world
    r = apply_inbound(db, "test-retail", [InboundMessage(from_addr="ahmed@zarafabrics.pk", subject="Automatic reply",
                                                          body="Out of office, back on 12 March.")], ledger, now=NOW)
    assert r.out_of_office == 1 and r.replied == 0
    zara = next(l for l in db.list_leads("test-retail") if l.company_name == "Zara Fabrics")
    assert zara.sequence_status == SequenceStatus.EMAIL_1_SENT and zara.reply_label == "out_of_office"
    assert zara.next_contact_at == datetime(2027, 3, 13, tzinfo=timezone.utc)
    assert zara.lead_id not in {l.lead_id for l in due_leads(db, "test-retail", datetime(2027, 3, 10, tzinfo=timezone.utc))}
    assert zara.lead_id in {l.lead_id for l in due_leads(db, "test-retail", datetime(2027, 3, 14, tzinfo=timezone.utc))}


def test_auto_reply_is_ignored(world):
    db, s, ledger, campaign = world
    r = apply_inbound(db, "test-retail", [InboundMessage(from_addr="info@mcc.com.pk", subject="Thank you for contacting MCC",
                                                          body="We have received your message and will respond shortly.")], ledger, now=NOW)
    assert r.auto_reply == 1 and r.replied == 0
    mcc = next(l for l in db.list_leads("test-retail") if l.company_name == "MCC")
    assert mcc.sequence_status == SequenceStatus.EMAIL_1_SENT and mcc.reply_label == "auto_reply"


def test_not_interested_stops_and_suppresses(world):
    db, s, ledger, campaign = world
    r = apply_inbound(db, "test-retail", [InboundMessage(from_addr="x@vegas.pk", subject="Re:", body="No thanks, not interested.")], ledger, now=NOW)
    assert r.not_interested == 1
    v = next(l for l in db.list_leads("test-retail") if l.company_name == "Vegas")
    assert v.sequence_status == SequenceStatus.REPLIED and v.reply_label == "not_interested"
    assert db.is_suppressed("x@vegas.pk")


def test_wrong_person_records_referral_for_approval(world):
    db, s, ledger, campaign = world
    r = apply_inbound(db, "test-retail", [InboundMessage(from_addr="ahmed@zarafabrics.pk", subject="Re: Zara Fabrics",
                                                          body="Not the right person - please contact Sana Malik (sana.malik@zarafabrics.pk).")], ledger, now=NOW)
    assert r.wrong_person == 1
    zara = next(l for l in db.list_leads("test-retail") if l.company_name == "Zara Fabrics")
    assert zara.sequence_status == SequenceStatus.REPLIED and zara.reply_label == "wrong_person"
    assert zara.referred_contact == {"name": "Sana Malik", "email": "sana.malik@zarafabrics.pk", "status": "pending"}
    assert "Not the right person" in zara.reply_excerpt
    assert not db.is_suppressed("ahmed@zarafabrics.pk")   # wrong person is not a refusal


def test_interested_is_flagged_and_stops(world):
    db, s, ledger, campaign = world
    r = apply_inbound(db, "test-retail", [InboundMessage(from_addr="ahmed@zarafabrics.pk", subject="Re:", body="Yes, let's schedule a call.")], ledger, now=NOW)
    assert r.interested == 1 and r.replied == 1
    zara = next(l for l in db.list_leads("test-retail") if l.company_name == "Zara Fabrics")
    assert zara.reply_label == "interested" and zara.sequence_status == SequenceStatus.REPLIED
