"""BUYER / VENDOR / UNKNOWN gate. Deterministic and explainable: every decision carries
the matched terms and where they matched. Nothing reaches contact discovery or
outreach without passing here."""

from __future__ import annotations

import re
from dataclasses import dataclass

from gtm_engine.config.schema import CampaignConfig, DefaultRules
from gtm_engine.models import Classification, CompanyType

# Weight of a hit by where it appears. Identity fields describe what the company *is*;
# body text describes what it talks about (and includes footers, vendor credits, ads).
W_IDENTITY = 3
W_BODY = 1
IDENTITY_CHARS = 800  # leading chars of about/home text treated as identity


@dataclass
class TextBundle:
    name: str
    title: str | None
    description: str | None
    about_text: str | None
    body_text: str
    category: str | None = None  # discovery category, e.g. "shop=clothes"
    site_reachable: bool = True  # False -> only the discovery record is available
    tender_terms: list[str] | None = None  # campaign terms matched in a live tender (PPRA)

    @property
    def identity(self) -> str:
        parts = [self.name, self.title or "", self.description or "", (self.about_text or "")[:IDENTITY_CHARS]]
        return " ".join(parts).lower()

    @property
    def body(self) -> str:
        return self.body_text.lower()


def _find_terms(text: str, terms: list[str]) -> list[str]:
    hits: list[str] = []
    for term in terms:
        if not term:
            continue
        # Word-boundary match; multi-word terms match as a phrase.
        pattern = r"(?<![a-z0-9])" + re.escape(term.lower()) + r"(?![a-z0-9])"
        if re.search(pattern, text):
            hits.append(term)
    return hits


_STOPWORDS = {"the", "a", "an", "of", "for", "and", "in", "to", "services", "service", "company", "companies"}
_STEM_LEN = 5  # common-prefix length treated as "same word" (agency/agencies, consult/consulting)


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", text.lower()) if w not in _STOPWORDS and len(w) > 2}


def _stem(word: str) -> str:
    return word[:_STEM_LEN] if len(word) >= _STEM_LEN else word


def _terms_matching(candidates: list[str], target_stems: set[str]) -> set[str]:
    """Candidate vendor terms (negative_keywords / vendor_phrases / vendor_categories) that
    share a word stem with the campaign's own target_industries/buyer_keywords."""
    if not target_stems:
        return set()
    matched = set()
    for term in candidates:
        if any(_stem(w) in target_stems for w in _words(term)):
            matched.add(term)
    return matched


class BuyerClassifier:
    def __init__(self, campaign: CampaignConfig, defaults: DefaultRules):
        # A campaign whose own target_industries/buyer_keywords names an agency/consultancy-type
        # business ("find marketing agencies") is deliberately searching for that business type
        # as its BUYER - but defaults.negative_keywords/vendor_phrases/vendor_categories reject
        # agencies/consultancies by default (they usually sell services rather than buy them).
        # Without this, any such campaign auto-rejects almost everything it finds, since a real
        # marketing agency's own name/site will say "agency" and "marketing services" about
        # itself. Stem-matching (common 5-char prefix) catches agency/agencies, consult/consulting
        # etc. without needing exact-string overlap. This mirrors allowed_vendor_keywords exactly -
        # it just derives the list automatically from what the campaign already says it wants,
        # instead of requiring the user to repeat it by hand.
        target_stems = {_stem(w) for w in _words(" ".join(campaign.target_industries + campaign.buyer_keywords))}
        auto_allowed = (_terms_matching(defaults.negative_keywords, target_stems)
                        | _terms_matching(defaults.vendor_phrases, target_stems)
                        | _terms_matching(defaults.vendor_categories, target_stems))
        allowed = set(campaign.allowed_vendor_keywords) | auto_allowed
        self._allowed = allowed
        self.vendor_terms = [t for t in dict.fromkeys(defaults.negative_keywords + campaign.negative_keywords)
                             if t not in allowed]
        self.vendor_phrases = [t for t in defaults.vendor_phrases if t not in allowed]
        self._negative_i18n = defaults.negative_keywords_i18n
        self._phrases_i18n = defaults.vendor_phrases_i18n
        self.buyer_terms = list(dict.fromkeys(campaign.buyer_keywords + campaign.target_industries))
        self.osm_categories = set(campaign.osm_categories)
        self.overture_categories = list(campaign.overture_categories)
        self.vendor_categories = {c for c in defaults.vendor_categories if c not in allowed}

    def _vendor_terms_for(self, language: str | None) -> tuple[list[str], list[str]]:
        """Vendor negative-keywords and self-description phrases to match against, widened
        with the detected page language's translated lists (if we maintain one for it).
        English pages (or undetected language) use the base lists unchanged."""
        if not language or language == "en":
            return self.vendor_terms, self.vendor_phrases
        extra_terms = [t for t in self._negative_i18n.get(language, []) if t not in self._allowed]
        extra_phrases = [t for t in self._phrases_i18n.get(language, []) if t not in self._allowed]
        terms = self.vendor_terms + [t for t in extra_terms if t not in self.vendor_terms]
        phrases = self.vendor_phrases + [t for t in extra_phrases if t not in self.vendor_phrases]
        return terms, phrases

    def classify(self, bundle: TextBundle, language: str | None = None) -> Classification:
        identity, body = bundle.identity, bundle.body
        reasons: list[str] = []
        vendor_terms, vendor_phrases = self._vendor_terms_for(language)

        vendor_name = _find_terms(bundle.name.lower(), vendor_terms)
        vendor_id = _find_terms(identity, vendor_terms)
        vendor_body = [t for t in _find_terms(body, vendor_terms) if t not in vendor_id]
        phrase_hits = _find_terms(body, vendor_phrases)
        buyer_id = _find_terms(identity, self.buyer_terms)
        buyer_body = [t for t in _find_terms(body, self.buyer_terms) if t not in buyer_id]

        vendor_score = W_IDENTITY * len(vendor_id) + W_BODY * len(vendor_body) + 0.5 * len(phrase_hits)
        buyer_score = W_IDENTITY * len(buyer_id) + W_BODY * min(len(buyer_body), 4)

        category_match = False
        if bundle.category:
            key = bundle.category.split("=")[0]
            if bundle.category in self.vendor_categories:
                reasons.append(f"discovery category '{bundle.category}' is a service-vendor category")
                return Classification(company_type=CompanyType.VENDOR, confidence=0.8, reasons=reasons,
                                      buyer_hits=buyer_id + buyer_body, vendor_hits=[bundle.category] + vendor_id + vendor_body)
            category_match = bundle.category in self.osm_categories or f"{key}=*" in self.osm_categories
            if key == "overture":
                value = bundle.category.split("=", 1)[1].lower()
                category_match = any(c in value for c in self.overture_categories)
            if category_match:
                reasons.append(f"discovery category '{bundle.category}' matches campaign target")

        # A category match means discovery already confirmed this business IS the type the
        # campaign is looking for (e.g. overture=advertising_agency for a "find marketing
        # agencies" campaign) - stronger, more reliable evidence than word-matching against
        # generic vendor-phrase lists, which any real agency's own site will trip (it describes
        # its own services using the same vocabulary those lists are built from: "marketing
        # services", "consulting", "lead generation"). Folded into buyer_score before the
        # vendor_id/vendor_score compare below so a confirmed category match isn't overridden by
        # a target business merely describing itself normally.
        if category_match:
            buyer_score += W_IDENTITY

        if vendor_name:
            reasons.append("vendor terms in company name: " + ", ".join(vendor_name))
        elif vendor_id:
            reasons.append("vendor terms in company identity: " + ", ".join(vendor_id))
        if vendor_body:
            reasons.append("vendor terms in page text: " + ", ".join(vendor_body[:6]))
        if len(phrase_hits) >= 2:
            reasons.append("sells-to-businesses phrasing: " + ", ".join(phrase_hits[:5]))
        if buyer_id:
            reasons.append("buyer terms in company identity: " + ", ".join(buyer_id))
        if buyer_body:
            reasons.append("buyer terms in page text: " + ", ".join(buyer_body[:6]))

        # --- decision ---------------------------------------------------------
        # A live public tender for what the campaign sells is buyer evidence by definition:
        # a stated requirement with a deadline from the procuring organisation itself.
        if bundle.tender_terms and not vendor_name:
            reasons.insert(0, "live tender matches campaign terms: " + ", ".join(bundle.tender_terms))
            return Classification(company_type=CompanyType.BUYER, confidence=0.9, reasons=reasons,
                                  buyer_hits=list(bundle.tender_terms) + buyer_id + buyer_body, vendor_hits=vendor_body + phrase_hits)

        # The name says what the company is. "Retail Growth Consultancy" serves retailers;
        # it is not one, however often "retail" appears on its pages.
        if vendor_name:
            return Classification(company_type=CompanyType.VENDOR, confidence=0.9, reasons=reasons,
                                  buyer_hits=buyer_id + buyer_body, vendor_hits=vendor_id + vendor_body + phrase_hits)

        # A vendor term in the identity is decisive unless the identity is even more
        # clearly a buyer (e.g. "ABC Retail Consulting" is a vendor; "XYZ Store - IT
        # solutions for our customers" is ambiguous and falls to the score compare).
        if vendor_id and vendor_score >= buyer_score:
            conf = min(1.0, 0.6 + 0.1 * len(vendor_id))
            return Classification(company_type=CompanyType.VENDOR, confidence=conf, reasons=reasons,
                                  buyer_hits=buyer_id + buyer_body, vendor_hits=vendor_id + vendor_body + phrase_hits)

        # Body-only vendor evidence must be substantial before it outweighs buyer evidence.
        if not buyer_id and not category_match and (len(vendor_body) >= 3 or (vendor_body and len(phrase_hits) >= 3)):
            return Classification(company_type=CompanyType.VENDOR, confidence=0.55, reasons=reasons,
                                  buyer_hits=buyer_body, vendor_hits=vendor_body + phrase_hits)

        # Name and category tags are supporting evidence. Without a reachable site there is
        # nothing to confirm them against, so the company stays UNKNOWN (spec: insufficient
        # evidence -> no outreach, keep for review). Vendor rules above still apply.
        if not bundle.site_reachable:
            reasons.append("website unavailable: discovery record alone is insufficient evidence")
            return Classification(company_type=CompanyType.UNKNOWN, confidence=0.3, reasons=reasons,
                                  buyer_hits=[], vendor_hits=vendor_body + phrase_hits)

        if buyer_id or category_match or len(buyer_body) >= 2:
            margin = buyer_score - vendor_score
            conf = max(0.5, min(1.0, 0.5 + margin / 10))
            if vendor_body or phrase_hits:
                reasons.append("buyer evidence outweighs weaker vendor mentions")
            return Classification(company_type=CompanyType.BUYER, confidence=round(conf, 2), reasons=reasons,
                                  buyer_hits=buyer_id + buyer_body, vendor_hits=vendor_body + phrase_hits)

        if not reasons:
            reasons.append("no buyer or vendor evidence found in available pages")
        return Classification(company_type=CompanyType.UNKNOWN, confidence=0.0, reasons=reasons,
                              buyer_hits=buyer_body, vendor_hits=vendor_body + phrase_hits)
