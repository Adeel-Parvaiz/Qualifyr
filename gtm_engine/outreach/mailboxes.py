"""Mailbox pool: several Gmail accounts, each with its own credentials, cap, warm-up state
and bounce guard. Email 1 goes out from the least-loaded mailbox; follow-ups always leave
from the mailbox that started the thread.

Environment layout (any number of mailboxes):
    GTM_MAILBOX_1_USER=a@gmail.com   GTM_MAILBOX_1_PASSWORD=...        (App Password)
                                  or GTM_MAILBOX_1_CLIENT_ID / _CLIENT_SECRET / _REFRESH_TOKEN (OAuth2)
    GTM_MAILBOX_1_LIMIT=40  (optional per-mailbox daily cap)   GTM_MAILBOX_1_NAME="Sender Name"
    GTM_MAILBOX_2_USER=...
The legacy single-mailbox variables (GTM_SMTP_USER / GTM_SMTP_PASSWORD / GTM_GMAIL_*) are
read as mailbox #1 when no numbered mailbox is defined."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from gtm_engine.outreach.config import OutreachSettings

log = logging.getLogger(__name__)


@dataclass
class Mailbox:
    address: str
    password: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    refresh_token: str | None = None
    daily_limit: int | None = None      # overrides settings.daily_limit
    sender_name: str | None = None      # overrides settings.sender_name
    enabled: bool = True

    @property
    def oauth(self) -> tuple[str, str, str] | None:
        if self.client_id and self.client_secret and self.refresh_token:
            return self.client_id, self.client_secret, self.refresh_token
        return None

    @property
    def auth_mode(self) -> str:
        return "oauth2" if self.oauth else "app_password" if self.password else "none"

    @property
    def can_send(self) -> bool:
        return self.enabled and self.auth_mode != "none"


def load_mailboxes(env: dict[str, str] | None = None, max_slots: int = 10) -> list[Mailbox]:
    """Slot 1 is GTM_MAILBOX_1_* or, when absent, the legacy GTM_SMTP_* / GTM_GMAIL_* variables.
    Slots 2..max_slots are GTM_MAILBOX_N_*; gaps are allowed."""
    env = env if env is not None else os.environ
    boxes: list[Mailbox] = []
    for i in range(1, max_slots + 1):
        p = f"GTM_MAILBOX_{i}_"
        if env.get(p + "USER"):
            limit = env.get(p + "LIMIT")
            boxes.append(Mailbox(
                address=env[p + "USER"].strip().lower(),
                password=env.get(p + "PASSWORD") or None,
                client_id=env.get(p + "CLIENT_ID") or None,
                client_secret=env.get(p + "CLIENT_SECRET") or None,
                refresh_token=env.get(p + "REFRESH_TOKEN") or None,
                daily_limit=int(limit) if limit else None,
                sender_name=env.get(p + "NAME") or None,
                enabled=env.get(p + "ENABLED", "true").lower() not in ("0", "false", "no"),
            ))
        elif i == 1 and env.get("GTM_SMTP_USER"):
            boxes.append(Mailbox(
                address=env["GTM_SMTP_USER"].strip().lower(),
                password=env.get("GTM_SMTP_PASSWORD") or None,
                client_id=env.get("GTM_GMAIL_CLIENT_ID") or None,
                client_secret=env.get("GTM_GMAIL_CLIENT_SECRET") or None,
                refresh_token=env.get("GTM_GMAIL_REFRESH_TOKEN") or None,
            ))
    # The same address twice (legacy + numbered) is one mailbox.
    seen: set[str] = set()
    return [b for b in boxes if not (b.address in seen or seen.add(b.address))]


def mailboxes_from_db(rows: list[dict], decrypt_fn=None) -> list[Mailbox]:
    """Build Mailbox instances from user_mailboxes DB rows (encrypted passwords)."""
    boxes: list[Mailbox] = []
    for row in rows:
        if not row.get("enabled", True):
            continue
        pw = None
        if row.get("encrypted_password") and decrypt_fn:
            try:
                pw = decrypt_fn(row["encrypted_password"])
            except Exception:
                log.warning("failed to decrypt mailbox password for %s", row["address"])
                continue
        boxes.append(Mailbox(
            address=row["address"],
            password=pw,
            daily_limit=row.get("daily_limit"),
            sender_name=row.get("sender_name"),
            enabled=True,
        ))
    return boxes


@dataclass
class MailboxState:
    mailbox: Mailbox
    day: str
    days_active: int | None
    cap: int
    sent_today: int
    bounced_today: int
    paused_reason: str | None = None

    @property
    def remaining(self) -> int:
        return 0 if self.paused_reason else max(self.cap - self.sent_today, 0)

    @property
    def load(self) -> float:
        return self.sent_today / self.cap if self.cap else 1.0

    def as_dict(self) -> dict:
        return {"address": self.mailbox.address, "auth_mode": self.mailbox.auth_mode, "enabled": self.mailbox.enabled,
                "days_active": self.days_active, "cap": self.cap, "sent_today": self.sent_today,
                "bounced_today": self.bounced_today, "remaining": self.remaining, "paused_reason": self.paused_reason}


class MailboxPool:
    """Owns one Sender per mailbox and the per-mailbox budgets for a day."""

    def __init__(self, mailboxes: list[Mailbox], settings: OutreachSettings, outbox: Path,
                 dry_run: bool = False, sender_factory=None):
        self.mailboxes = [m for m in mailboxes if m.enabled]
        self.settings = settings
        self.outbox = outbox
        self.dry_run = dry_run or not any(m.can_send for m in self.mailboxes)
        self._senders: dict[str, object] = {}
        self._factory = sender_factory
        if not self.mailboxes:
            # Nothing configured: a single dry-run identity so the pipeline still exercises end to end.
            self.mailboxes = [Mailbox(address="dryrun@example.invalid", enabled=True)]
            self.dry_run = True

    @property
    def name(self) -> str:
        return "dry-run" if self.dry_run else "smtp-pool"

    def addresses(self) -> list[str]:
        return [m.address for m in self.mailboxes]

    def get(self, address: str) -> Mailbox | None:
        return next((m for m in self.mailboxes if m.address == address.lower()), None)

    def sender_for(self, address: str):
        if address not in self._senders:
            box = self.get(address)
            if box is None:
                raise KeyError(address)
            if self._factory is not None:
                self._senders[address] = self._factory(box, self.dry_run)
            else:
                from gtm_engine.outreach.sender import DryRunSender, SmtpSender
                if self.dry_run or not box.can_send:
                    self._senders[address] = DryRunSender(self.settings, self.outbox, box.address)
                else:
                    self._senders[address] = SmtpSender.for_mailbox(box, self.settings)
        return self._senders[address]

    def close(self) -> None:
        for s in self._senders.values():
            if hasattr(s, "close"):
                s.close()

    # -- budgets ------------------------------------------------------------------------

    def states(self, db, campaign_id: str, ledger, day: str) -> dict[str, MailboxState]:
        ledger.legacy_mailbox = self.mailboxes[0].address
        out: dict[str, MailboxState] = {}
        for box in self.mailboxes:
            days_active = ledger.days_active(box.address, day)
            cap = self.settings.effective_daily_cap(days_active if days_active is not None else 1)
            if box.daily_limit:
                cap = min(cap, box.daily_limit)
            sent = ledger.sent_on(day, box.address)
            bounced = db.bounced_today(campaign_id, day, box.address, legacy_mailbox=self.mailboxes[0].address)
            paused = None
            if sent >= self.settings.min_sends_for_bounce_rate and bounced / max(sent, 1) > self.settings.max_bounce_rate:
                paused = f"bounce rate {bounced}/{sent} exceeds {self.settings.max_bounce_rate:.0%}"
            out[box.address] = MailboxState(box, day, days_active, cap, sent, bounced, paused)
        return out

    @staticmethod
    def pick_for_new_thread(states: dict[str, MailboxState]) -> MailboxState | None:
        """Least-loaded mailbox with budget left; ties broken by address for determinism."""
        eligible = [s for s in states.values() if s.remaining > 0]
        if not eligible:
            return None
        return min(eligible, key=lambda s: (s.load, s.mailbox.address))
