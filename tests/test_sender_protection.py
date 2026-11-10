"""Phase A: warm-up ramp, send jitter, bounce guard, Gmail OAuth2."""

import base64
from datetime import timedelta

import httpx
import pytest

from gtm_engine.models import CompanyType, EmailStatus, Lead, Priority, SequenceStatus
from gtm_engine.outreach.config import OutreachSettings, load_templates
from gtm_engine.outreach.gmail_oauth import AccessTokenProvider, xoauth2_b64, xoauth2_string
from gtm_engine.outreach.ledger import Ledger
from gtm_engine.outreach.sender import OutgoingEmail, SendResult, SmtpSender
from gtm_engine.outreach.sequencer import enqueue, send_due, stop_lead
from gtm_engine.storage.database import Database
from test_outreach import MON_10AM_PKT


class FakeSender:
    name = "fake"
    from_addr = "sender@gmail.com"

    def __init__(self):
        self.sent = []

    def send(self, email: OutgoingEmail) -> SendResult:
        self.sent.append(email)
        return SendResult(ok=True, message_id=f"<{email.lead_id}.{email.step}@t>")


def _leads(n: int) -> list[Lead]:
    return [Lead(campaign_id="test-retail", company_name=f"Co {i}", domain=f"co{i}.pk", city="Islamabad",
                 company_type=CompanyType.BUYER, total_score=90, priority=Priority.HIGH,
                 contact_email=f"owner@co{i}.pk", email_status=EmailStatus.MX_VALID, outreach_ready=True)
            for i in range(n)]


@pytest.fixture
def db(settings, campaign):
    d = Database(settings.database_url)
    d.upsert_campaign(campaign.campaign_id, campaign.name, campaign.model_dump(mode="json"))
    for l in _leads(12):
        d.save_lead(l, "run", l.domain)
    yield d
    d.close()


@pytest.fixture
def osettings():
    return OutreachSettings(daily_limit=10, warmup_enabled=True, warmup_start_per_day=2, warmup_step_per_day=3,
                            jitter_min_s=30, jitter_max_s=120, require_approval=False,
                            max_bounce_rate=0.10, min_sends_for_bounce_rate=5)


# --- warm-up ramp ---------------------------------------------------------------------------

def test_effective_cap_ramps_to_daily_limit(osettings):
    assert [osettings.effective_daily_cap(d) for d in (1, 2, 3, 4, 10)] == [2, 5, 8, 10, 10]
    osettings.warmup_enabled = False
    assert osettings.effective_daily_cap(1) == 10


def test_warmup_limits_first_days_and_grows(db, campaign, osettings, tmp_path):
    ledger = Ledger(tmp_path / "l.json")
    enqueue(db, "test-retail", osettings, ledger, now=MON_10AM_PKT)
    sender = FakeSender()
    sleeps = []
    day1 = send_due(db, campaign, osettings, load_templates(), sender, ledger, now=MON_10AM_PKT, sleep=sleeps.append)
    assert day1.sent == 2 and "warm-up day 1" in day1.stopped_reason
    assert ledger.first_send_day("sender@gmail.com") == MON_10AM_PKT.strftime("%Y-%m-%d")
    # same day again: nothing more
    again = send_due(db, campaign, osettings, load_templates(), sender, ledger, now=MON_10AM_PKT + timedelta(hours=2), sleep=sleeps.append)
    assert again.sent == 0
    # next day the cap is 5
    day2 = send_due(db, campaign, osettings, load_templates(), sender, ledger, now=MON_10AM_PKT + timedelta(days=1), sleep=sleeps.append)
    assert day2.sent == 5 and "warm-up day 2" in day2.stopped_reason
    # jitter: every pause is inside the configured band, and not all identical
    assert sleeps and all(30 <= s <= 120 for s in sleeps)
    assert len(set(round(s, 3) for s in sleeps)) > 1


def test_warmup_state_survives_via_ledger(tmp_path, osettings):
    ledger = Ledger(tmp_path / "l.json")
    ledger.note_send_day("a@gmail.com", "2027-03-01")
    reloaded = Ledger(tmp_path / "l.json")
    assert reloaded.days_active("a@gmail.com", "2027-03-03") == 3
    assert reloaded.days_active("other@gmail.com", "2027-03-03") is None


# --- bounce guard ---------------------------------------------------------------------------

def test_bounce_rate_pauses_mailbox_for_the_day(db, campaign, osettings, tmp_path):
    osettings.warmup_enabled = False
    ledger = Ledger(tmp_path / "l.json")
    enqueue(db, "test-retail", osettings, ledger, now=MON_10AM_PKT)
    sender = FakeSender()
    first = send_due(db, campaign, osettings, load_templates(), sender, ledger, limit=6, now=MON_10AM_PKT, sleep=lambda s: None)
    assert first.sent == 6
    # two of six bounce (33%) -> above the 10% guard
    sent_leads = [l for l in db.list_leads("test-retail") if l.sequence_status == SequenceStatus.EMAIL_1_SENT][:2]
    for l in sent_leads:
        stop_lead(db, l, SequenceStatus.BOUNCED, "bounce notification", ledger)
    later = send_due(db, campaign, osettings, load_templates(), sender, ledger, now=MON_10AM_PKT + timedelta(hours=1), sleep=lambda s: None)
    assert later.sent == 0 and "bounce rate 2/6" in later.stopped_reason and "paused" in later.stopped_reason
    # a new day resets the window
    tomorrow = send_due(db, campaign, osettings, load_templates(), sender, ledger, limit=1, now=MON_10AM_PKT + timedelta(days=1), sleep=lambda s: None)
    assert tomorrow.sent == 1


def test_bounce_guard_needs_minimum_sample(db, campaign, osettings, tmp_path):
    osettings.warmup_enabled = False
    ledger = Ledger(tmp_path / "l.json")
    enqueue(db, "test-retail", osettings, ledger, now=MON_10AM_PKT)
    sender = FakeSender()
    send_due(db, campaign, osettings, load_templates(), sender, ledger, limit=2, now=MON_10AM_PKT, sleep=lambda s: None)
    l = next(l for l in db.list_leads("test-retail") if l.sequence_status == SequenceStatus.EMAIL_1_SENT)
    stop_lead(db, l, SequenceStatus.BOUNCED, "bounce notification", ledger)  # 1 of 2 = 50%, but only 2 sent
    r = send_due(db, campaign, osettings, load_templates(), sender, ledger, limit=1, now=MON_10AM_PKT + timedelta(hours=1), sleep=lambda s: None)
    assert r.sent == 1


# --- OAuth2 -----------------------------------------------------------------------------------

def test_xoauth2_encoding():
    raw = xoauth2_string("me@gmail.com", "ya29.token")
    assert raw == "user=me@gmail.com\x01auth=Bearer ya29.token\x01\x01"
    assert base64.b64decode(xoauth2_b64("me@gmail.com", "ya29.token")).decode() == raw


def test_access_token_refresh_and_cache():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"access_token": "tok1", "expires_in": 3600})

    provider = AccessTokenProvider("cid", "secret", "refresh", http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert provider.token() == "tok1"
    assert provider.token() == "tok1"
    assert len(calls) == 1
    body = calls[0].content.decode()
    assert "grant_type=refresh_token" in body and "refresh_token=refresh" in body


def test_auth_mode_prefers_oauth(monkeypatch):
    monkeypatch.setenv("GTM_SMTP_USER", "me@gmail.com")
    monkeypatch.delenv("GTM_SMTP_PASSWORD", raising=False)
    s = OutreachSettings()
    assert s.auth_mode == "none" and not s.credentials_present
    monkeypatch.setenv("GTM_SMTP_PASSWORD", "app-pass")
    assert OutreachSettings().auth_mode == "app_password"
    monkeypatch.setenv("GTM_GMAIL_CLIENT_ID", "cid")
    monkeypatch.setenv("GTM_GMAIL_CLIENT_SECRET", "sec")
    monkeypatch.setenv("GTM_GMAIL_REFRESH_TOKEN", "ref")
    assert OutreachSettings().auth_mode == "oauth2"


def test_smtp_sender_uses_xoauth2_command(monkeypatch):
    monkeypatch.setenv("GTM_SMTP_USER", "me@gmail.com")
    monkeypatch.setenv("GTM_GMAIL_CLIENT_ID", "cid")
    monkeypatch.setenv("GTM_GMAIL_CLIENT_SECRET", "sec")
    monkeypatch.setenv("GTM_GMAIL_REFRESH_TOKEN", "ref")

    class FakeTokens:
        def token(self):
            return "tok"

    class FakeSMTP:
        commands: list[tuple[str, str]] = []
        sent: list = []

        def __init__(self, *a, **k): pass
        def ehlo(self): pass
        def starttls(self): pass
        def docmd(self, cmd, arg=""):
            self.commands.append((cmd, arg))
            return 235, b"ok"
        def login(self, *a):
            raise AssertionError("password login must not be used with OAuth")
        def send_message(self, msg):
            self.sent.append(msg)
        def quit(self): pass

    monkeypatch.setattr("gtm_engine.outreach.sender.smtplib.SMTP", FakeSMTP)
    s = SmtpSender(OutreachSettings(), token_provider=FakeTokens())
    assert s.name == "smtp-oauth2"
    r = s.send(OutgoingEmail(to="a@b.pk", subject="s", body="b", lead_id="l", step="email_1"))
    assert r.ok and FakeSMTP.commands[0][0] == "AUTH"
    assert FakeSMTP.commands[0][1] == "XOAUTH2 " + xoauth2_b64("me@gmail.com", "tok")


def test_legacy_ledger_infers_first_send_day(tmp_path):
    ledger = Ledger(tmp_path / "l.json")
    ledger.record_sent("a@x.pk", "email_1", "<m1>", "l1")
    ledger.data["sent"]["a@x.pk"]["email_1"]["at"] = "2027-02-20T05:00:00+00:00"
    ledger.record_sent("b@x.pk", "email_1", "<m2>", "l2")
    ledger.data["sent"]["b@x.pk"]["email_1"]["at"] = "2027-02-25T05:00:00+00:00"
    assert ledger.first_send_day("any@gmail.com") == "2027-02-20"
    assert ledger.days_active("any@gmail.com", "2027-03-01") == 10
