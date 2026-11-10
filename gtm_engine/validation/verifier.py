"""Email existence verification without any paid third-party dependency.

Backends, in preference order chosen by `build_verifier`:
  direct   - SMTP handshake to the recipient's MX (RCPT TO, no message sent). Default. Needs
             outbound port 25, which most ISPs and serverless runners block; probed once at
             start. When port 25 isn't reachable, degrades to mx_only rather than failing.
  reacher  - self-hosted Reacher (check-if-email-exists) over HTTP: GTM_REACHER_URL. Opt-in.
  mx_only  - the domain accepts mail, nothing known about the mailbox. Final fallback.

(Hunter.io was previously a paid-API fallback here; it was removed on 2026-10-09 along with
every mention of GTM_HUNTER_API_KEY. The engine is now zero paid-verification-API by default.)

Every backend returns a VerifyResult with a status the outreach gate understands:
  deliverable  mailbox confirmed          -> may be emailed
  risky        catch-all / unknown answer -> discovered candidates are NOT emailed
  invalid      mailbox rejected           -> never emailed
  unverified   no verifier could run
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import smtplib
import socket
import string
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

import dns.asyncresolver
import dns.exception
import httpx

log = logging.getLogger(__name__)


class VerifyStatus(str, Enum):
    DELIVERABLE = "deliverable"
    RISKY = "risky"
    INVALID = "invalid"
    UNVERIFIED = "unverified"


@dataclass
class VerifyResult:
    status: VerifyStatus
    backend: str
    reason: str = ""
    catch_all: bool | None = None


class EmailVerifier(Protocol):
    name: str

    async def verify(self, email: str) -> VerifyResult: ...

    async def is_catch_all(self, domain: str) -> bool | None: ...


def _random_local() -> str:
    return "zq" + "".join(random.choices(string.ascii_lowercase + string.digits, k=14))


# --------------------------------------------------------------------------- direct SMTP

class DirectSmtpVerifier:
    name = "direct"

    def __init__(self, helo: str = "qualifyr.local", mail_from: str = "probe@qualifyr.local",
                 timeout_s: float = 10.0):
        self.helo = helo
        self.mail_from = mail_from
        self.timeout_s = timeout_s
        self._resolver = dns.asyncresolver.Resolver()
        self._resolver.lifetime = timeout_s
        self._catch_all: dict[str, bool | None] = {}

    async def _mx(self, domain: str) -> str | None:
        try:
            answers = await self._resolver.resolve(domain, "MX")
            return sorted((r.preference, str(r.exchange).rstrip(".")) for r in answers)[0][1]
        except dns.exception.DNSException:
            return None

    def _rcpt(self, mx: str, email: str) -> tuple[int, str]:
        s = smtplib.SMTP(timeout=self.timeout_s)
        try:
            s.connect(mx, 25)
            s.ehlo(self.helo)
            s.mail(self.mail_from)
            code, msg = s.rcpt(email)
            return code, msg.decode(errors="replace") if isinstance(msg, bytes) else str(msg)
        finally:
            try:
                s.quit()
            except (smtplib.SMTPException, OSError):
                pass

    async def is_catch_all(self, domain: str) -> bool | None:
        domain = domain.lower()
        if domain in self._catch_all:
            return self._catch_all[domain]
        mx = await self._mx(domain)
        if not mx:
            self._catch_all[domain] = None
            return None
        try:
            code, _ = await asyncio.to_thread(self._rcpt, mx, f"{_random_local()}@{domain}")
            result = 200 <= code < 300
        except (smtplib.SMTPException, OSError, socket.timeout):
            result = None
        self._catch_all[domain] = result
        return result

    async def verify(self, email: str) -> VerifyResult:
        domain = email.split("@", 1)[1].lower()
        mx = await self._mx(domain)
        if not mx:
            return VerifyResult(VerifyStatus.INVALID, self.name, "no MX")
        catch_all = await self.is_catch_all(domain)
        try:
            code, msg = await asyncio.to_thread(self._rcpt, mx, email)
        except (smtplib.SMTPException, OSError, socket.timeout) as exc:
            return VerifyResult(VerifyStatus.UNVERIFIED, self.name, f"smtp error: {type(exc).__name__}", catch_all)
        if 200 <= code < 300:
            if catch_all:
                return VerifyResult(VerifyStatus.RISKY, self.name, "catch-all domain accepts anything", True)
            return VerifyResult(VerifyStatus.DELIVERABLE, self.name, f"{code} {msg[:60]}", catch_all)
        if code in (550, 551, 553) or "not exist" in msg.lower() or "unknown" in msg.lower():
            return VerifyResult(VerifyStatus.INVALID, self.name, f"{code} {msg[:60]}", catch_all)
        return VerifyResult(VerifyStatus.RISKY, self.name, f"{code} {msg[:60]}", catch_all)


async def port25_reachable(host: str = "gmail-smtp-in.l.google.com", timeout_s: float = 5.0) -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, 25), timeout_s)
        writer.close()
        return True
    except (OSError, asyncio.TimeoutError):
        return False


# --------------------------------------------------------------------------- Reacher (HTTP)

class ReacherVerifier:
    name = "reacher"

    def __init__(self, base_url: str, timeout_s: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout_s)
        self._catch_all: dict[str, bool | None] = {}

    async def _check(self, email: str) -> dict | None:
        try:
            r = await self._client.post(f"{self.base_url}/v0/check_email", json={"to_email": email})
        except httpx.HTTPError as exc:
            log.warning("reacher: %s", exc)
            return None
        return r.json() if r.status_code == 200 else None

    async def is_catch_all(self, domain: str) -> bool | None:
        if domain not in self._catch_all:
            data = await self._check(f"{_random_local()}@{domain}")
            self._catch_all[domain] = bool((data or {}).get("smtp", {}).get("is_catch_all")) if data else None
        return self._catch_all[domain]

    async def verify(self, email: str) -> VerifyResult:
        data = await self._check(email)
        if not data:
            return VerifyResult(VerifyStatus.UNVERIFIED, self.name, "reacher unreachable")
        smtp = data.get("smtp") or {}
        catch_all = bool(smtp.get("is_catch_all"))
        status = {"safe": VerifyStatus.DELIVERABLE, "invalid": VerifyStatus.INVALID,
                  "risky": VerifyStatus.RISKY}.get(data.get("is_reachable"), VerifyStatus.UNVERIFIED)
        return VerifyResult(status, self.name, str(data.get("is_reachable")), catch_all)

    async def close(self) -> None:
        await self._client.aclose()


# --------------------------------------------------------------------------- MX only

class MxOnlyVerifier:
    """Keeps today's behaviour: nothing is confirmed, nothing is guessed."""

    name = "mx_only"

    async def verify(self, email: str) -> VerifyResult:
        return VerifyResult(VerifyStatus.UNVERIFIED, self.name, "no mailbox-level verifier available")

    async def is_catch_all(self, domain: str) -> bool | None:
        return None


async def build_verifier(mode: str = "direct", reacher_url: str | None = None) -> EmailVerifier:
    """Pick a verifier backend. Default is `direct` SMTP (own-infrastructure, no paid API).
    `auto` keeps the old preference order minus Hunter: reacher if configured, else direct if
    port 25 is reachable, else MX-only."""
    reacher_url = reacher_url or os.environ.get("GTM_REACHER_URL")
    if mode == "off":
        return MxOnlyVerifier()
    if mode in ("auto", "reacher") and reacher_url:
        return ReacherVerifier(reacher_url)
    if mode in ("auto", "direct"):
        if await port25_reachable():
            return DirectSmtpVerifier()
        if mode == "direct":
            log.warning("direct SMTP verifier requested but port 25 is blocked here; MX-only")
    if mode not in ("auto", "direct"):
        log.warning("email verifier '%s' not available; falling back to MX-only", mode)
    else:
        log.info("no mailbox-level verifier available (port 25 blocked, no Reacher); MX-only")
    return MxOnlyVerifier()
