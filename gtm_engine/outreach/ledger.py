"""Durable send ledger, committed to the repo by CI. The SQLite DB can be lost between
GitHub runners; this file cannot, so it is the final guard against sending Email 1
twice to the same address."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from gtm_engine.models import utcnow


class Ledger:
    def __init__(self, path: Path):
        self.path = path
        # Records written before mailbox tracking carry no mailbox; they belong to the first
        # configured mailbox (set by MailboxPool), never to a mailbox added later.
        self.legacy_mailbox: str | None = None
        self.data: dict = {"sent": {}, "stopped": {}, "mailboxes": {}}
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                self.data = {"sent": loaded.get("sent", {}), "stopped": loaded.get("stopped", {}),
                             "mailboxes": loaded.get("mailboxes", {})}
            except json.JSONDecodeError:
                pass

    # -- queries ------------------------------------------------------------

    def has_sent(self, email: str, step: str) -> bool:
        return step in self.data["sent"].get(email.lower(), {})

    def steps_sent(self, email: str) -> dict[str, dict]:
        return self.data["sent"].get(email.lower(), {})

    def is_stopped(self, email: str) -> bool:
        return email.lower() in self.data["stopped"]

    # -- mailbox warm-up state ----------------------------------------------

    def first_send_day(self, mailbox: str) -> str | None:
        recorded = self.data["mailboxes"].get(mailbox.lower(), {}).get("first_send_day")
        if recorded:
            return recorded
        # Ledgers written before mailbox tracking existed: infer from that mailbox's (or
        # untagged) earliest send.
        dates = [rec["at"][:10] for steps in self.data["sent"].values() for rec in steps.values()
                 if rec.get("at") and self._owner(rec, mailbox) == mailbox.lower()]
        return min(dates) if dates else None

    def _owner(self, rec: dict, fallback: str) -> str:
        return (rec.get("mailbox") or self.legacy_mailbox or fallback).lower()

    def note_send_day(self, mailbox: str, day: str) -> None:
        box = self.data["mailboxes"].setdefault(mailbox.lower(), {})
        if not box.get("first_send_day"):
            box["first_send_day"] = day
            self.save()

    def days_active(self, mailbox: str, today: str) -> int | None:
        """1 on the first sending day, 2 the next calendar day, ... None before any send."""
        first = self.first_send_day(mailbox)
        if not first:
            return None
        from datetime import date
        return (date.fromisoformat(today) - date.fromisoformat(first)).days + 1

    def sent_on(self, day_prefix: str, mailbox: str | None = None) -> int:
        """Sends on a day; with `mailbox`, only that mailbox's (legacy records without a
        mailbox count toward the first configured mailbox, passed as `mailbox`)."""
        n = 0
        for steps in self.data["sent"].values():
            for rec in steps.values():
                if not rec.get("at", "").startswith(day_prefix):
                    continue
                if mailbox is None or self._owner(rec, mailbox) == mailbox.lower():
                    n += 1
        return n

    # -- mutations -----------------------------------------------------------

    def record_sent(self, email: str, step: str, message_id: str | None, lead_id: str,
                    at: datetime | None = None, mailbox: str | None = None) -> None:
        rec = self.data["sent"].setdefault(email.lower(), {})
        rec[step] = {"at": (at or utcnow()).isoformat(), "message_id": message_id, "lead_id": lead_id,
                     "mailbox": (mailbox or "").lower() or None}
        self.save()

    def mailbox_of(self, email: str) -> str | None:
        return (self.steps_sent(email).get("email_1") or {}).get("mailbox")

    def record_stop(self, email: str, reason: str) -> None:
        self.data["stopped"][email.lower()] = {"reason": reason, "at": utcnow().isoformat()}
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1, sort_keys=True), encoding="utf-8")
