"""Email senders. `SmtpSender` talks to Gmail (STARTTLS + App Password). `DryRunSender`
writes .eml files instead, and is what you get whenever credentials are absent."""

from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path
from typing import Protocol

from gtm_engine.outreach.config import OutreachSettings
from gtm_engine.outreach.gmail_oauth import AccessTokenProvider, credentials_from_env, xoauth2_b64

log = logging.getLogger(__name__)


@dataclass
class OutgoingEmail:
    to: str
    subject: str
    body: str
    in_reply_to: str | None = None   # Message-ID of email 1 for threaded follow-ups
    lead_id: str = ""
    step: str = ""


@dataclass
class SendResult:
    ok: bool
    message_id: str | None
    error: str | None = None


class Sender(Protocol):
    name: str

    def send(self, email: OutgoingEmail) -> SendResult: ...


def build_message(email: OutgoingEmail, settings: OutreachSettings, from_addr: str,
                  sender_name: str | None = None) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr((sender_name or settings.sender_name, from_addr))
    msg["To"] = email.to
    msg["Subject"] = email.subject
    msg["Message-ID"] = make_msgid(domain=from_addr.split("@", 1)[1])
    msg["Reply-To"] = settings.reply_to or from_addr
    # One-click stop path; also what the reply sync looks for.
    msg["List-Unsubscribe"] = f"<mailto:{settings.reply_to or from_addr}?subject=STOP>"
    if email.in_reply_to:
        msg["In-Reply-To"] = email.in_reply_to
        msg["References"] = email.in_reply_to
    msg["X-GTM-Lead"] = email.lead_id
    msg["X-GTM-Step"] = email.step
    msg.set_content(email.body)
    return msg


class DryRunSender:
    name = "dry-run"

    def __init__(self, settings: OutreachSettings, outbox: Path, from_addr: str = "dryrun@example.invalid"):
        self.settings = settings
        self.outbox = outbox
        self.from_addr = from_addr
        outbox.mkdir(parents=True, exist_ok=True)

    def send(self, email: OutgoingEmail) -> SendResult:
        msg = build_message(email, self.settings, self.from_addr)
        path = self.outbox / f"{email.lead_id}_{email.step}.eml"
        path.write_bytes(bytes(msg))
        log.info("dry-run: wrote %s (to %s)", path.name, email.to)
        return SendResult(ok=True, message_id=msg["Message-ID"])


class SmtpSender:
    name = "smtp"

    def __init__(self, settings: OutreachSettings, token_provider: AccessTokenProvider | None = None,
                 *, from_addr: str | None = None, password: str | None = None, sender_name: str | None = None):
        self.settings = settings
        self.from_addr = from_addr or settings.smtp_user
        self._password = password if from_addr else settings.smtp_password
        self._sender_name = sender_name
        self._conn: smtplib.SMTP | None = None
        self._tokens = token_provider
        if self._tokens is None and not from_addr and settings.oauth_present:
            self._tokens = AccessTokenProvider(*credentials_from_env())
        if not self.from_addr or not (self._tokens or self._password):
            raise RuntimeError("mailbox needs an address plus OAuth2 credentials or an App Password")
        self.name = "smtp-oauth2" if self._tokens else "smtp"

    @classmethod
    def for_mailbox(cls, box, settings: OutreachSettings) -> "SmtpSender":
        tokens = AccessTokenProvider(*box.oauth) if box.oauth else None
        return cls(settings, tokens, from_addr=box.address, password=box.password, sender_name=box.sender_name)

    def _connect(self) -> smtplib.SMTP:
        if self._conn is None:
            conn = smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=30)
            conn.ehlo()
            conn.starttls()
            conn.ehlo()
            if self._tokens is not None:
                # XOAUTH2: no password ever leaves the environment; tokens expire hourly.
                auth = xoauth2_b64(self.from_addr, self._tokens.token())
                code, resp = conn.docmd("AUTH", "XOAUTH2 " + auth)
                if code != 235:
                    raise smtplib.SMTPAuthenticationError(code, resp)
            else:
                conn.login(self.from_addr, self._password)
            self._conn = conn
        return self._conn

    def send(self, email: OutgoingEmail) -> SendResult:
        msg = build_message(email, self.settings, self.from_addr, self._sender_name)
        try:
            self._connect().send_message(msg)
        except smtplib.SMTPRecipientsRefused as exc:
            return SendResult(ok=False, message_id=None, error=f"recipient_refused:{exc.recipients}")
        except (smtplib.SMTPException, OSError) as exc:
            self.close()
            return SendResult(ok=False, message_id=None, error=f"{type(exc).__name__}:{exc}")
        return SendResult(ok=True, message_id=msg["Message-ID"])

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.quit()
            except (smtplib.SMTPException, OSError):
                pass
            self._conn = None


def make_sender(settings: OutreachSettings, outbox: Path, force_dry_run: bool = False) -> Sender:
    if force_dry_run or not settings.credentials_present:
        if not force_dry_run:
            log.warning("no SMTP credentials in environment: running in dry-run mode (outbox=%s)", outbox)
        return DryRunSender(settings, outbox, settings.smtp_user or "dryrun@example.invalid")
    return SmtpSender(settings)
