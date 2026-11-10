"""Offer -> discovery targets (E1).

The reframe's promise is that you describe what you sell and the engine figures out where to
look. Until now that translation was done by the *user*, in OSM/Overture tag syntax: a
campaign with a great offer but no hand-picked categories discovered nothing. This turns the
offer into the categories to search, so discovery is driven by the offer, not by the user's
knowledge of map tags.

How it stays honest: categories come from a curated taxonomy when possible, or from a
validated niche fallback that checks against known-valid OSM tags. The deterministic path
matches sector keywords against the offer; the optional LLM refines which sectors apply.
When no sector matches, industries are mapped to valid OSM/Overture tags directly."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from difflib import get_close_matches
from functools import lru_cache
from pathlib import Path

import yaml

from gtm_engine.config.loader import DEFAULTS_DIR
from gtm_engine.llm.client import LLM, parse_json_object
from gtm_engine.llm.tasks import generate_search_queries

log = logging.getLogger(__name__)

TAXONOMY_PATH = DEFAULTS_DIR / "discovery_taxonomy.yaml"
VALID_OSM_TAGS_PATH = DEFAULTS_DIR / "valid_osm_tags.yaml"


@dataclass
class DiscoveryTargets:
    osm_categories: list[str] = field(default_factory=list)
    overture_categories: list[str] = field(default_factory=list)
    sectors: list[str] = field(default_factory=list)
    search_queries: list[str] = field(default_factory=list)  # E2: queries for web-search discovery

    @property
    def empty(self) -> bool:
        return not self.osm_categories and not self.overture_categories


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        norm = (it or "").strip()
        low = norm.lower()
        if norm and low not in seen:
            seen.add(low)
            out.append(norm)
    return out


def _seed_queries(industries: list[str] | None, cities: list[str] | None,
                  sectors: list[str], taxonomy: dict, limit: int,
                  areas: list[str] | None = None) -> list[str]:
    """Deterministic web-search queries from the campaign's industries (or, lacking those, the
    human term of each matched sector) crossed with areas+cities. Always something to search
    even without an LLM."""
    terms = list(industries or [])
    if not terms:
        for s in sectors:
            if s == "_niche":
                continue
            match = (taxonomy.get(s) or {}).get("match", [])
            if match:
                terms.append(match[0])
    city_list = (cities or [])[:2]
    area_list = (areas or [])[:3]
    queries: list[str] = []
    for term in terms[:4]:
        if area_list and city_list:
            for area in area_list:
                for city in city_list[:1]:
                    queries.append(f"{term} {area} {city}")
        elif city_list:
            for city in city_list:
                queries.append(f"{term} in {city}")
        else:
            queries.append(f"{term} companies")
    return _dedupe(queries)[:limit]


def load_taxonomy(path: Path | None = None) -> dict:
    data = yaml.safe_load((path or TAXONOMY_PATH).read_text(encoding="utf-8")) or {}
    return data.get("sectors", {})


_VALID_OSM_CACHE: dict[str, set[str]] | None = None


def load_valid_osm_tags(path: Path | None = None) -> dict[str, set[str]]:
    global _VALID_OSM_CACHE  # noqa: PLW0603
    if _VALID_OSM_CACHE is not None:
        return _VALID_OSM_CACHE
    try:
        data = yaml.safe_load((path or VALID_OSM_TAGS_PATH).read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        log.warning("valid_osm_tags.yaml not found at %s", path or VALID_OSM_TAGS_PATH)
        return {}
    result = {k: set(v) for k, v in data.items() if isinstance(v, list)}
    _VALID_OSM_CACHE = result
    return result


_INDUSTRY_SYNONYMS: dict[str, list[str]] = {
    "newspaper": ["newspaper", "newsagent"],
    "media": ["newspaper", "newsagent"],
    "law": ["lawyer"],
    "legal": ["lawyer"],
    "attorney": ["lawyer"],
    "travel": ["travel_agency", "travel_agent"],
    "watch": ["watches", "watchmaker", "clockmaker"],
    "tailor": ["tailor", "dressmaker"],
    "printing": ["printer", "copyshop"],
    "print": ["printer", "copyshop"],
    "flower": ["florist"],
    "pet": ["pet", "pet_grooming", "veterinary"],
    "photo": ["photo", "photographer"],
    "barber": ["hairdresser"],
    "hair": ["hairdresser"],
    "laundry": ["laundry", "dry_cleaning"],
    "plumber": ["plumber"],
    "electrician": ["electrician"],
    "carpenter": ["carpenter", "cabinet_maker"],
    "mechanic": ["car_repair"],
    "dentist": ["dentist"],
    "doctor": ["doctors", "doctor", "clinic"],
    "hospital": ["hospital"],
    "school": ["school"],
    "gym": ["fitness_centre"],
    "fitness": ["fitness_centre"],
    "hotel": ["hotel"],
    "motel": ["motel"],
    "bank": ["bank"],
    "insurance": ["insurance"],
    "real estate": ["estate_agent"],
    "property": ["estate_agent", "property_management"],
    "accountant": ["accountant"],
    "architect": ["architect"],
    "courier": ["courier"],
    "locksmith": ["locksmith"],
    "agency": ["advertising_agency"],
    "agencies": ["advertising_agency"],
    "advertising": ["advertising_agency"],
    "marketing": ["advertising_agency"],
}


def _niche_fallback(industries: list[str], valid_tags: dict[str, set[str]]) -> DiscoveryTargets:
    """Construct valid OSM/Overture categories from raw industry terms when no taxonomy sector
    matches. Uses synonym lookup, fuzzy matching against valid tags, and heuristic key probing."""
    osm: list[str] = []
    overture: list[str] = []
    all_values = []
    for key, vals in valid_tags.items():
        for v in vals:
            all_values.append((key, v))

    for industry in industries[:5]:
        term = industry.lower().strip()
        found_osm: list[str] = []

        words_to_try = [term] + term.split()
        for w in words_to_try:
            synonyms = _INDUSTRY_SYNONYMS.get(w, [])
            for syn in synonyms:
                for key, vals in valid_tags.items():
                    if syn in vals and f"{key}={syn}" not in found_osm:
                        found_osm.append(f"{key}={syn}")

        if not found_osm:
            for w in words_to_try:
                for key, vals in valid_tags.items():
                    if w in vals and f"{key}={w}" not in found_osm:
                        found_osm.append(f"{key}={w}")

        if not found_osm:
            for w in words_to_try:
                singular = w.rstrip("s") if w.endswith("s") and len(w) > 3 else w
                for key, vals in valid_tags.items():
                    if singular in vals and f"{key}={singular}" not in found_osm:
                        found_osm.append(f"{key}={singular}")

        if not found_osm:
            val_list = [v for _, v in all_values]
            close = get_close_matches(term, val_list, n=3, cutoff=0.75)
            for match in close:
                for key, vals in valid_tags.items():
                    if match in vals and f"{key}={match}" not in found_osm:
                        found_osm.append(f"{key}={match}")

        for tag in found_osm[:5]:
            if tag not in osm:
                osm.append(tag)
            # The matched OSM tag value (e.g. "advertising_agency") is a far more specific
            # Overture substring than the raw industry phrase: Overture's own taxonomy tends to
            # share these OSM-derived value names, while a multi-word phrase like "social media
            # agencies" rarely appears verbatim in Overture's category strings.
            value = tag.split("=", 1)[1]
            if value not in overture:
                overture.append(value)

        overture.append(term)
        if term != term.rstrip("s"):
            overture.append(term.rstrip("s"))

    if not osm and not overture:
        return DiscoveryTargets()
    return DiscoveryTargets(
        osm_categories=osm,
        overture_categories=_dedupe(overture),
        sectors=["_niche"],
    )


def _expand(sectors: list[str], taxonomy: dict) -> DiscoveryTargets:
    osm: list[str] = []
    overture: list[str] = []
    for key in sectors:
        spec = taxonomy.get(key) or {}
        for c in spec.get("osm", []):
            if c not in osm:
                osm.append(c)
        for c in spec.get("overture", []):
            if c not in overture:
                overture.append(c)
    return DiscoveryTargets(osm_categories=osm, overture_categories=overture, sectors=list(sectors))


@lru_cache(maxsize=2048)
def _term_variants(term: str) -> tuple[str, ...]:
    """A match term plus its singular/plural counterpart.

    Whole-word matching alone is too strict to replace substring matching: the taxonomy says
    "pharmacy" and "shop" while campaigns say "pharmacies" and "shops", and dropping those
    matches is just as wrong as the over-matching this is here to prevent."""
    base = term.strip().lower()
    out = [base]
    if base.endswith("ies") and len(base) > 4:
        out.append(base[:-3] + "y")
    elif base.endswith("es") and len(base) > 4:
        out += [base[:-1], base[:-2]]
    elif base.endswith("s") and len(base) > 3:
        out.append(base[:-1])
    else:
        out.append(base + "s")
        if base.endswith(("s", "x", "z", "ch", "sh")):
            out.append(base + "es")
        if base.endswith("y") and len(base) > 2:
            out.append(base[:-1] + "ies")
    return tuple(dict.fromkeys(v for v in out if v))


@lru_cache(maxsize=2048)
def _term_pattern(term: str) -> re.Pattern[str]:
    alts = "|".join(re.escape(v) for v in _term_variants(term))
    return re.compile(rf"\b(?:{alts})\b")


def _matches_term(text: str, terms: list) -> bool:
    """Whether any taxonomy match term occurs in `text` as a whole word (either number).

    Plain substring matching sent the 2026-10-13 Karachi run looking for car dealers, because
    the term "car" sits inside "card payment links". Short terms are common in the taxonomy
    ("car", "pos", "gym", "spa"), so every one of them is a trap for this."""
    return any(_term_pattern(str(term)).search(text) for term in terms)


def match_sectors(offer: str, industries: list[str] | None, taxonomy: dict) -> list[str]:
    """Sectors whose `match` terms appear in the offer or the campaign's industries. The broad
    `general_retail` fallback is only kept when nothing more specific matched, so a clothing
    campaign is not diluted with every storefront."""
    text = " ".join([offer or "", *(industries or [])]).lower()
    hits: list[str] = []
    for key, spec in taxonomy.items():
        if key == "general_retail":
            continue
        if _matches_term(text, spec.get("match", [])):
            hits.append(key)
    if not hits and "general_retail" in taxonomy:
        general = taxonomy["general_retail"]
        if _matches_term(text, general.get("match", [])):
            hits.append("general_retail")
    return hits


def resolve_sectors(offer: str, industries: list[str] | None, taxonomy: dict) -> list[str]:
    """The sectors a campaign actually targets.

    When the campaign names its target industries, THOSE decide what to search for. The offer
    describes what we sell and the buyer's current situation, so sectors it matches incidentally
    are guesses about buyers - the same thing _llm_sectors produces, and just as damaging once
    they become discovery targets. The 2026-10-13 Karachi run ("...for restaurants that currently
    take delivery orders only over the phone...", industries=["restaurants"]) matched electronics
    on "phone" and so searched mobile_phone_store and computer across Karachi: 11 of its 15
    outreach-ready "restaurants" were computer and electronics shops.

    Only applied when the industries themselves match something specific - if they are too vague
    for the taxonomy, the offer's match is still better than nothing, and an empty specific match
    drops through to the niche fallback in derive_discovery_targets().

    Shared with the NL parser so the sectors shown at the confirmation prompt are the sectors the
    run will really use; computing them in two places let the prompt advertise `electronics` for a
    restaurant campaign that never searched it."""
    det_sectors = match_sectors(offer, industries, taxonomy)
    if not industries:
        return det_sectors
    from_industries = match_sectors("", industries, taxonomy)
    if not [s for s in from_industries if s != "general_retail"]:
        return det_sectors
    if set(from_industries) != set(det_sectors):
        log.info("sectors from named industries %s: %s (offer alone also matched %s)",
                 industries, from_industries,
                 [s for s in det_sectors if s not in from_industries])
    return from_industries


async def _llm_sectors(llm: LLM, offer: str, taxonomy: dict) -> list[str]:
    keys = list(taxonomy.keys())
    system = ("You select which business sectors would BUY the seller's offer, choosing ONLY "
              "from the provided sector list. Pick the sectors a plausible buyer belongs to, "
              "not the seller's own sector. Output a JSON array of sector keys, nothing else.")
    user = f"Seller offer: {offer!r}\n\nSectors to choose from:\n{keys}\n\nJSON array of keys."
    try:
        raw = await llm.complete(system, user, max_tokens=200)
    except Exception as exc:  # noqa: BLE001 - the LLM is optional
        log.debug("llm sector selection failed: %s", exc)
        return []
    obj = parse_json_object("{\"k\":" + raw + "}") if raw.strip().startswith("[") else None
    picked = obj.get("k") if obj else None
    if not isinstance(picked, list):
        # tolerate a bare array or comma/space separated keys
        picked = [k for k in keys if k in (raw or "")]
    return [k for k in picked if k in taxonomy]


async def _llm_discovery_categories(llm: LLM, industries: list[str],
                                     valid_tags: dict[str, set[str]]) -> DiscoveryTargets:
    """Ask the LLM to suggest OSM/Overture categories for niche industries, validated against
    the known-valid tag list. Returns empty on failure (deterministic niche fallback already ran)."""
    tag_ref = {k: sorted(v)[:30] for k, v in valid_tags.items()}
    system = (
        "You suggest OSM map tags and Overture category substrings for discovering businesses. "
        "Output a JSON object: {\"osm\": [\"key=value\", ...], \"overture\": [\"substring\", ...]}. "
        "OSM tags MUST be from the valid list provided. Overture categories are free substrings."
    )
    user = (
        f"Find businesses of type: {industries}\n\n"
        f"Valid OSM tags (key: [values]):\n{tag_ref}\n\n"
        "Suggest OSM tags and Overture substrings for these business types. JSON only."
    )
    try:
        raw = await llm.complete(system, user, max_tokens=400)
    except Exception as exc:  # noqa: BLE001
        log.debug("llm discovery categories failed: %s", exc)
        return DiscoveryTargets()
    obj = parse_json_object(raw)
    if not obj:
        return DiscoveryTargets()
    osm = []
    for tag in (obj.get("osm") or []):
        if "=" not in str(tag):
            continue
        key, val = str(tag).split("=", 1)
        if key in valid_tags and val in valid_tags[key] and tag not in osm:
            osm.append(tag)
    overture = [str(c).strip().lower() for c in (obj.get("overture") or []) if c]
    return DiscoveryTargets(osm_categories=osm, overture_categories=overture, sectors=["_niche"])


async def derive_discovery_targets(offer: str, industries: list[str] | None = None,
                                   llm: LLM | None = None, taxonomy: dict | None = None,
                                   cities: list[str] | None = None, countries: list[str] | None = None,
                                   areas: list[str] | None = None,
                                   max_search_queries: int = 8) -> DiscoveryTargets:
    """The categories AND web-search queries to search for this offer. Categories come from the
    taxonomy when possible, or from a validated niche fallback / LLM suggestions for industries
    outside the 16 taxonomy sectors. Returns empty when there is no offer."""
    if not (offer or "").strip():
        return DiscoveryTargets()
    taxonomy = taxonomy if taxonomy is not None else load_taxonomy()
    # Deterministic match against the taxonomy's curated sectors, kept separate from the LLM's
    # sector guesses below: the LLM is asked "which sectors would BUY this offer", which for a
    # campaign that already names an explicit target_industries (e.g. "social media agencies")
    # can surface unrelated buyer-type sectors (e.g. general_retail, hospitality - businesses
    # that might buy social-media software) having nothing to do with the named industry. If
    # those guesses were folded into `sectors` before the niche-fallback check below, they would
    # make `sectors` look non-empty and silently block the industry-specific niche fallback that
    # should run instead - so niche fallback is gated on the deterministic match only.
    det_sectors = resolve_sectors(offer, industries, taxonomy)
    # A deterministic match of ONLY the broad general_retail fallback is not a real match. Its
    # match terms are "retail, store, shop, brand, outlet, seller, merchant", so it fires on any
    # offer that merely contains one of those words in passing - the 2026-10-13 Virginia run hit
    # it on "brand" inside "one branded report". When the campaign names its target industries,
    # that accident must not pre-empt the industry-specific niche fallback below.
    specific = [s for s in det_sectors if s != "general_retail"]
    sectors = list(det_sectors)
    # The LLM is asked "which sectors would BUY this offer", so its answers describe buyer types
    # and have nothing to do with an industry the user named explicitly. They are only useful for
    # an offer-only campaign. Merging them when industries ARE named makes those sectors campaign
    # *targets*, and BuyerClassifier matches overture categories by substring - "store" matches
    # "flowers_and_gifts_store", "clinic" matches "urgent_care_clinic" - which is how gift shops
    # and urgent-care clinics qualified as social media agencies. Fixed once on 2026-10-12 for the
    # empty-deterministic-match path; the general_retail path had exactly the same hole.
    if llm is not None and not industries:
        for k in await _llm_sectors(llm, offer, taxonomy):
            if k not in sectors:
                sectors.append(k)

    if not specific and industries:
        valid_tags = load_valid_osm_tags()
        targets = _niche_fallback(industries, valid_tags)
        if llm is not None and valid_tags:
            llm_cats = await _llm_discovery_categories(llm, industries, valid_tags)
            for tag in llm_cats.osm_categories:
                if tag not in targets.osm_categories:
                    targets.osm_categories.append(tag)
            for cat in llm_cats.overture_categories:
                if cat not in targets.overture_categories:
                    targets.overture_categories.append(cat)
        # The LLM's sector guesses are deliberately NOT merged in here. They answer "which
        # sectors would BUY this offer", so for a campaign that already names its target
        # industry they are unrelated buyer-type sectors (restaurants, clinics, general retail
        # for a "social media agencies" search). Merging them was tried and reverted on
        # 2026-10-12: these categories do not merely widen discovery, they become campaign
        # *targets*, and BuyerClassifier matches overture categories by substring - so "store"
        # matched "flowers_and_gifts_store" and "clinic" matched "urgent_care_clinic",
        # qualifying gift shops, coffee shops and urgent-care clinics across Virginia as social
        # media agencies. When an explicit industry resolves to real niche categories, those
        # categories alone are the target.
        if not targets.empty:
            log.info("niche fallback for industries %s: osm=%s overture=%s",
                     industries, targets.osm_categories, targets.overture_categories)
        else:
            targets = _expand(["general_retail"], taxonomy) if "general_retail" in taxonomy else DiscoveryTargets()
            targets.sectors = ["general_retail"]
    elif not sectors and "general_retail" in taxonomy:
        targets = _expand(["general_retail"], taxonomy)
        targets.sectors = ["general_retail"]
    else:
        targets = _expand(sectors, taxonomy)

    region = ", ".join((cities or [])[:2] + (countries or [])[:1]) or None
    queries = _seed_queries(industries, cities, targets.sectors, taxonomy,
                            limit=max_search_queries, areas=areas)
    queries += await generate_search_queries(llm, offer, region)
    targets.search_queries = _dedupe(queries)[:max_search_queries]

    log.info("derived discovery targets from offer: sectors=%s osm=%d overture=%d queries=%d",
             targets.sectors, len(targets.osm_categories), len(targets.overture_categories),
             len(targets.search_queries))
    return targets
