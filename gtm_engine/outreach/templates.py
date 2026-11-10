"""Render a template for a lead. Every placeholder is filled from observed data or a
configured neutral fallback. Nothing is invented."""

from __future__ import annotations

import re
from dataclasses import dataclass

from gtm_engine.config.schema import CampaignConfig
from gtm_engine.models import Lead
from gtm_engine.outreach.config import OutreachSettings, Templates

_HOOK_PHRASES = {
    "sells online": "you sell online",
    "operates multiple branches/outlets": "you run several outlets",
    "currently hiring": "you're hiring",
    "recently expanding": "you're expanding",
    "takes orders over WhatsApp/DM": "you take orders over WhatsApp",
}


def first_name(contact_name: str | None) -> str | None:
    if not contact_name:
        return None
    parts = [p for p in re.split(r"\s+", contact_name.strip()) if p]
    honorifics = {"dr", "dr.", "mr", "mr.", "mrs", "mrs.", "ms", "ms.", "engr", "engr.", "prof", "prof.", "syed", "muhammad", "mohammad"}
    while parts and parts[0].lower() in honorifics and len(parts) > 1:
        parts.pop(0)
    return parts[0].strip(",.") if parts else None


def hook_sentence(hook: str | None) -> str:
    """'based in Islamabad; sells online; runs on shopify' -> ' I noticed you sell online.'"""
    if not hook:
        return ""
    facts = [f.strip() for f in hook.split(";")]
    phrased = [_HOOK_PHRASES[f] for f in facts if f in _HOOK_PHRASES]
    tech = next((f for f in facts if f.startswith("runs on ")), None)
    if tech:
        phrased.append(f"your store {tech}")
    if not phrased:
        return ""
    if len(phrased) == 1:
        joined = phrased[0]
    else:
        joined = ", ".join(phrased[:-1]) + " and " + phrased[-1]
    return f" I noticed {joined}."


@dataclass
class RenderedEmail:
    subject: str
    body: str
    missing: list[str]  # placeholders that used a fallback


def build_context(lead: Lead, campaign: CampaignConfig, settings: OutreachSettings, templates: Templates) -> tuple[dict, list[str]]:
    fb = templates.fallbacks
    missing: list[str] = []

    def pick(key: str, value: str | None) -> str:
        if value:
            return value
        missing.append(key)
        return fb.get(key, "")

    fn = first_name(lead.contact_name)
    ctx = {
        "first_name": pick("first_name", fn),
        "contact_name": pick("contact_name", lead.contact_name) if lead.contact_name else fb.get("first_name", "there"),
        "company_name": lead.company_name,
        "city": pick("city", lead.city),
        "offer": campaign.offer[:1].lower() + campaign.offer[1:],  # used mid-sentence
        "sender_name": settings.sender_name,
        "personalization_hook": lead.personalization_hook or "",
        "hook_sentence": hook_sentence(lead.personalization_hook),
        "landing_url": settings.landing_url or "",
    }
    return ctx, missing


class _Safe(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def render(step: str, lead: Lead, campaign: CampaignConfig, settings: OutreachSettings, templates: Templates) -> RenderedEmail:
    tpl = templates.for_step(step)
    ctx, missing = build_context(lead, campaign, settings, templates)
    subject = tpl.subject.format_map(_Safe(ctx)).strip()
    body = tpl.body.format_map(_Safe(ctx)).rstrip() + "\n" + templates.footer.format_map(_Safe(ctx)).rstrip() + "\n"
    leftovers = re.findall(r"\{([a-z_]+)\}", subject + body)
    if leftovers:
        raise ValueError(f"template '{step}' has unknown placeholders: {sorted(set(leftovers))}")
    return RenderedEmail(subject=subject, body=body, missing=missing)
