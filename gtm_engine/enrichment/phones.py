"""Pakistani phone classification. A mobile (03xx / +92 3xx) is almost always the owner's
own number at an SME; a landline is the shop counter. Pure regex, no lookup service."""

from __future__ import annotations

import re
from dataclasses import dataclass

_DIGITS = re.compile(r"\D+")

# PTA mobile prefixes: 030x–034x (Jazz/Warid, Zong, Ufone, Telenor, SCOM).
_PK_MOBILE_PREFIXES = tuple(f"3{d}" for d in "01234")

# Landline area codes: 2–3 digits after the leading 0 (e.g. 051 Islamabad, 042 Lahore, 021 Karachi).
_PK_LANDLINE = {"21", "22", "41", "42", "44", "48", "51", "52", "53", "55", "56", "57", "61", "62",
                "63", "64", "65", "66", "67", "68", "71", "74", "81", "86", "91", "92", "94", "95",
                "222", "233", "235", "238", "242", "243", "244", "297", "298", "332", "336", "337",
                "343", "404", "442", "453", "454", "457", "459", "462", "463", "466", "467", "468",
                "469", "471", "472", "474", "475", "476", "477", "478", "483", "484", "485", "486",
                "521", "522", "523", "524", "525", "526", "527", "528", "532", "536", "542", "543",
                "547", "548", "562", "566", "567", "574", "576", "577", "578", "579", "581", "582",
                "583", "588", "585", "584", "586", "587", "596", "594", "593", "598", "608", "606",
                "604", "656", "661", "633", "676", "672", "685", "686", "694", "695", "696", "709",
                "722", "723", "726", "816", "823", "824", "826", "828", "832", "833", "835", "837",
                "838", "843", "844", "847", "848", "852", "853", "855", "856", "914", "915", "916",
                "919", "922", "923", "924", "925", "926", "927", "928", "929", "932", "937", "938",
                "939", "942", "943", "944", "945", "946", "963", "965", "966", "969", "981", "996",
                "997", "998"}


@dataclass(frozen=True)
class PhoneInfo:
    raw: str
    e164: str | None      # +92XXXXXXXXXX when Pakistani and well-formed
    kind: str             # mobile | landline | unknown


def classify_phone(raw: str | None) -> PhoneInfo | None:
    if not raw:
        return None
    digits = _DIGITS.sub("", raw)
    if digits.startswith("0092"):
        digits = digits[2:]
    if digits.startswith("92"):
        national = digits[2:]
    elif digits.startswith("0"):
        national = digits[1:]
    else:
        return PhoneInfo(raw, None, "unknown")
    if len(national) == 10 and national.startswith(_PK_MOBILE_PREFIXES):
        return PhoneInfo(raw, f"+92{national}", "mobile")
    for n in (3, 2):
        if national[:n] in _PK_LANDLINE and 9 <= len(national) <= 10:
            return PhoneInfo(raw, f"+92{national}", "landline")
    return PhoneInfo(raw, f"+92{national}" if 9 <= len(national) <= 10 else None, "unknown")


def best_phone(phones: list[str]) -> PhoneInfo | None:
    """Prefer a mobile, then a landline, then anything."""
    infos = [i for i in (classify_phone(p) for p in phones) if i]
    for kind in ("mobile", "landline", "unknown"):
        for i in infos:
            if i.kind == kind:
                return i
    return None
