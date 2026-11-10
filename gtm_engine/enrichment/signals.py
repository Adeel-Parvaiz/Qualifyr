"""Buying signals, pain signals and technology markers from crawled pages.
Keyword-driven from config/defaults/signals.yaml; nothing inferred beyond the text."""

from __future__ import annotations

import re

from gtm_engine.config.schema import DefaultRules
from gtm_engine.models import CompanyQuality, Signals
from gtm_engine.scraping.site_crawler import SiteSnapshot


def _phrase_hits(text: str, groups: dict[str, list[str]]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for group, phrases in groups.items():
        hits = [p for p in phrases if re.search(r"(?<![a-z0-9])" + re.escape(p.lower()) + r"(?![a-z0-9])", text)]
        if hits:
            out[group] = hits
    return out


def detect_signals(snapshot: SiteSnapshot, defaults: DefaultRules) -> Signals:
    text = snapshot.all_text.lower()
    html = " ".join(snapshot.raw_html.values()).lower()
    buying = _phrase_hits(text, defaults.buying_signal_keywords)
    if "careers" in snapshot.pages:
        buying.setdefault("hiring", []).append("careers page exists")
    pain = _phrase_hits(text, defaults.pain_signal_keywords)
    technologies = [name for name, markers in defaults.technology_markers.items()
                    if any(m.lower() in html for m in markers)]
    return Signals(buying=buying, pain=pain, technologies=technologies)


def website_belongs_to(company_name: str, domain: str | None, snapshot: SiteSnapshot) -> bool:
    """Discovery sources sometimes carry a wrong website (a directory, an old tenant).
    A domain or homepage title that does not reflect the company name is suspicious."""
    from gtm_engine.discovery.search import name_matches  # local import: avoids a cycle
    if not domain or not snapshot.reachable:
        return True  # nothing to compare; unreachable sites are already penalised
    home = snapshot.pages.get("home")
    return name_matches(company_name, domain, home.title if home else None)


def assess_quality(snapshot: SiteSnapshot, company_name: str | None = None, domain: str | None = None) -> CompanyQuality:
    q = CompanyQuality(
        reachable=snapshot.reachable,
        https=snapshot.https,
        has_contact_page="contact" in snapshot.pages,
        has_about_page="about" in snapshot.pages,
        has_public_email=bool(snapshot.emails),
        has_phone=bool(snapshot.phones),
        page_count=len(snapshot.pages),
    )
    if not q.reachable:
        if snapshot.integrity_reason:
            q.notes.append(f"website rejected: {snapshot.integrity_detail or snapshot.integrity_reason}")
        else:
            q.notes.append(f"website unreachable ({snapshot.error})")
    if q.reachable and not q.https:
        q.notes.append("no https")
    if q.reachable and q.page_count <= 1:
        q.notes.append("single-page site")
    if not q.has_public_email and not q.has_phone:
        q.notes.append("no public contact details")
    home = snapshot.pages.get("home")
    if home and len(home.text) < 200:
        q.notes.append("very thin homepage content")
    if company_name and not website_belongs_to(company_name, domain, snapshot):
        q.website_mismatch = True
        q.notes.append("website does not appear to belong to this company")
    if snapshot.reachable:
        from gtm_engine.enrichment.external_signals import site_quality
        sq = site_quality(snapshot.raw_html)
        q.copyright_year, q.mobile_friendly = sq.copyright_year, sq.mobile_friendly
        q.notes.extend(sq.notes)
    return q


def summarize(signals: Signals) -> tuple[str | None, str | None]:
    """(buying_signal, pain_signal) one-liners for the CSV."""
    buying = "; ".join(f"{g}: {', '.join(h[:2])}" for g, h in signals.buying.items()) or None
    pain = "; ".join(f"{g}: {', '.join(h[:2])}" for g, h in signals.pain.items()) or None
    return buying, pain
