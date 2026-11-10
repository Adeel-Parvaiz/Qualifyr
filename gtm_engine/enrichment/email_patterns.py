"""Decision-maker email discovery.

Given a named decision-maker and the company domain, build candidate addresses from
common patterns, ranked by (1) the pattern any *known* personal address on that domain
already follows, then (2) global frequency. Candidates are only ever accepted when a
verifier returns `deliverable`; a catch-all domain makes every candidate `risky` and
nothing is accepted. Unverified guesses never become the contact email."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from gtm_engine.validation.emails import is_generic_mailbox
from gtm_engine.validation.verifier import EmailVerifier, VerifyResult, VerifyStatus

# Ordered by how often they appear at SMEs; the first that fits wins when nothing is known.
PATTERNS = ("first.last", "first", "firstlast", "flast", "first_last", "firstl", "last.first", "f.last", "last")

_HONORIFICS = {"dr", "mr", "mrs", "ms", "engr", "prof", "haji", "hafiz"}
# Prefix given names: "Muhammad Usman Khan" is addressed as Usman, so mailboxes follow usman.*
_PREFIX_NAMES = {"muhammad", "mohammad", "mohammed", "mohd", "syed", "mian", "ch", "chaudhry", "sheikh"}
_ASCII = re.compile(r"[^a-z]")


@dataclass
class NameParts:
    first: str
    last: str


@dataclass
class Discovery:
    email: str | None
    status: VerifyStatus | None
    pattern: str | None
    tried: list[tuple[str, str]] = field(default_factory=list)  # (candidate, status)
    reason: str = ""
    catch_all: bool | None = None   # True when the verifier flagged the domain as catch-all


def name_parts(full_name: str) -> NameParts | None:
    tokens = [_ASCII.sub("", t.lower()) for t in full_name.replace(".", " ").split()]
    tokens = [t for t in tokens if t and t not in _HONORIFICS]
    if len(tokens) >= 3 and tokens[0] in _PREFIX_NAMES:
        tokens = tokens[1:]
    if len(tokens) < 2:
        return None
    return NameParts(first=tokens[0], last=tokens[-1])


def render(pattern: str, p: NameParts, domain: str) -> str:
    f, l = p.first, p.last
    local = {
        "first.last": f"{f}.{l}", "first": f, "firstlast": f"{f}{l}", "flast": f"{f[0]}{l}",
        "first_last": f"{f}_{l}", "firstl": f"{f}{l[0]}", "last.first": f"{l}.{f}",
        "f.last": f"{f[0]}.{l}", "last": l,
    }[pattern]
    return f"{local}@{domain}"


def infer_pattern(known_email: str, known_name: str | None) -> str | None:
    """If a personal address on this domain is public, learn its pattern."""
    if not known_name:
        return None
    p = name_parts(known_name)
    if not p:
        return None
    local = known_email.split("@", 1)[0].lower()
    for pattern in PATTERNS:
        if render(pattern, p, "x").split("@")[0] == local:
            return pattern
    return None


def candidates(full_name: str, domain: str, known_pattern: str | None = None,
               generic_prefixes: list[str] = ()) -> list[tuple[str, str]]:
    p = name_parts(full_name)
    if not p:
        return []
    order = ([known_pattern] if known_pattern else []) + [x for x in PATTERNS if x != known_pattern]
    out: list[tuple[str, str]] = []
    for pattern in order:
        email = render(pattern, p, domain)
        if email not in {e for e, _ in out} and not is_generic_mailbox(email, list(generic_prefixes)):
            out.append((email, pattern))
    return out


async def discover(full_name: str, domain: str, verifier: EmailVerifier, *,
                   known_pattern: str | None = None, generic_prefixes: list[str] = (),
                   max_tries: int = 5) -> Discovery:
    cands = candidates(full_name, domain, known_pattern, generic_prefixes)
    if not cands:
        return Discovery(None, None, None, reason="name unusable for patterns")
    if verifier.name == "mx_only":
        # No mailbox-level verifier: surface the best candidate for a human, never ship it.
        email, pattern = cands[0]
        return Discovery(email, VerifyStatus.UNVERIFIED, pattern, [(email, "unverified")],
                         reason="no verifier available; candidate shown, not sent")
    if await verifier.is_catch_all(domain):
        email, pattern = cands[0]
        return Discovery(email, VerifyStatus.RISKY, pattern, [(email, "risky")],
                         reason="catch-all domain: any address is accepted, cannot confirm",
                         catch_all=True)
    tried: list[tuple[str, str]] = []
    for email, pattern in cands[:max_tries]:
        result: VerifyResult = await verifier.verify(email)
        tried.append((email, result.status.value))
        if result.status == VerifyStatus.DELIVERABLE:
            return Discovery(email, VerifyStatus.DELIVERABLE, pattern, tried,
                             reason=f"confirmed by {result.backend}: {result.reason}")
        if result.status == VerifyStatus.UNVERIFIED:
            return Discovery(None, VerifyStatus.UNVERIFIED, None, tried, reason=result.reason)
    return Discovery(None, VerifyStatus.INVALID, None, tried, reason="no pattern confirmed")
