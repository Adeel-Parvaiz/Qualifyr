"""E1: the offer drives which map categories are searched, from a curated taxonomy so every
derived category is valid."""

from gtm_engine.discovery.targeting import (
    _niche_fallback, derive_discovery_targets, load_taxonomy, load_valid_osm_tags, match_sectors,
)


class FakeLLM:
    name = "fake"

    def __init__(self, reply: str):
        self.reply = reply

    async def complete(self, system, user, *, max_tokens=400):
        return self.reply


def test_taxonomy_loads_with_known_sectors():
    tax = load_taxonomy()
    assert "clothing_apparel" in tax and "electronics" in tax and "general_retail" in tax
    assert "shop=clothes" in tax["clothing_apparel"]["osm"]


def test_match_sectors_from_offer_text():
    tax = load_taxonomy()
    assert "clothing_apparel" in match_sectors("inventory software for clothing brands", None, tax)
    assert "electronics" in match_sectors("POS for mobile phone shops", None, tax)
    # A specific match must not also drag in the broad general_retail fallback.
    assert "general_retail" not in match_sectors("apparel retailers", None, tax)


def test_match_sectors_falls_back_to_general_retail():
    tax = load_taxonomy()
    assert match_sectors("software for shops and outlets", None, tax) == ["general_retail"]
    assert match_sectors("quantum widgets for nobody", None, tax) == []


async def test_derive_targets_deterministic_expands_to_valid_categories():
    t = await derive_discovery_targets("inventory software for clothing and footwear retailers", ["fashion"])
    assert "shop=clothes" in t.osm_categories
    assert "clothing" in t.overture_categories
    # The named industry decides the sectors, so "footwear" - which only the offer mentions - is
    # NOT searched. The offer describes what we sell and who currently has the problem; treating
    # its nouns as discovery targets is what sent a Karachi restaurant campaign looking for
    # computer shops. Narrowing to the stated industry is the deliberate trade.
    assert t.sectors == ["clothing_apparel"]


async def test_named_industries_outrank_sectors_the_offer_mentions_in_passing():
    """The 2026-10-13 Karachi regression: an offer for restaurants "that currently take delivery
    orders only over the phone" with "card payment links" matched electronics (on "phone") and
    automotive (on "car" inside "card"), and both became discovery targets - 11 of 15
    outreach-ready "restaurants" were computer and electronics shops."""
    offer = ("We set up an online ordering website with card payment links for restaurants "
             "that currently take delivery orders only over the phone")
    t = await derive_discovery_targets(offer, ["restaurants"])
    assert t.sectors == ["food_restaurant"]
    assert not [c for c in t.overture_categories
                if c in {"mobile_phone_store", "computer", "car_dealer", "auto_parts"}]


def test_match_sectors_requires_whole_words_not_substrings():
    tax = load_taxonomy()
    # "car" inside "card", and "pos" inside "position" - short taxonomy terms are all traps.
    assert "automotive" not in match_sectors("card payment links for cafes", None, tax)
    assert "automotive" in match_sectors("software for car dealers", None, tax)


def test_match_sectors_matches_singular_and_plural():
    tax = load_taxonomy()
    # The taxonomy says "pharmacy"/"shop"; campaigns say "pharmacies"/"shops".
    assert "pharmacy_health" in match_sectors("", ["pharmacies"], tax)
    assert match_sectors("software for shops and outlets", None, tax) == ["general_retail"]


async def test_derive_targets_never_leaves_an_offer_empty():
    t = await derive_discovery_targets("some very niche B2B thing")
    assert not t.empty and t.sectors == ["general_retail"]  # always something to search


async def test_llm_widens_sectors_but_only_valid_keys():
    # The model suggests one valid sector and one nonsense key; only the valid one is kept.
    llm = FakeLLM('["furniture_home", "made_up_sector"]')
    t = await derive_discovery_targets("home fit-out services", None, llm)
    assert "furniture_home" in t.sectors and "made_up_sector" not in t.sectors
    assert "shop=furniture" in t.osm_categories


async def test_no_offer_returns_empty():
    t = await derive_discovery_targets("")
    assert t.empty and t.sectors == []


def test_valid_osm_tags_loads():
    tags = load_valid_osm_tags()
    assert "shop" in tags and "office" in tags and "craft" in tags
    assert "watches" in tags["shop"]
    assert "newspaper" in tags["office"]
    assert "watchmaker" in tags["craft"]


def test_niche_fallback_newspaper():
    tags = load_valid_osm_tags()
    result = _niche_fallback(["newspaper"], tags)
    assert not result.empty
    assert result.sectors == ["_niche"]
    assert "office=newspaper" in result.osm_categories


def test_niche_fallback_watchmaker():
    tags = load_valid_osm_tags()
    result = _niche_fallback(["watch repair"], tags)
    assert not result.empty
    osm_str = " ".join(result.osm_categories)
    assert "watches" in osm_str or "watchmaker" in osm_str


def test_niche_fallback_travel_agency():
    tags = load_valid_osm_tags()
    result = _niche_fallback(["travel agency"], tags)
    assert not result.empty
    assert any("travel" in t for t in result.osm_categories)


def test_niche_fallback_unknown_returns_overture_only():
    tags = load_valid_osm_tags()
    result = _niche_fallback(["quantum widgets"], tags)
    assert "quantum widgets" in result.overture_categories


async def test_derive_targets_niche_uses_fallback_not_general_retail():
    t = await derive_discovery_targets("Find newspaper offices in Islamabad",
                                       industries=["newspaper offices"])
    assert "_niche" in t.sectors or "office=newspaper" in t.osm_categories
    assert "general_retail" not in t.sectors


async def test_named_industry_targets_exclude_llm_buyer_sector_guesses():
    """Regression (2026-10-12 Virginia run): the LLM answers "which sectors would BUY this
    offer", so for "social media agencies" it guesses restaurants/clinics/retail. Those were
    merged into the campaign's target categories, and BuyerClassifier substring-matches
    overture categories - so "store" matched "flowers_and_gifts_store" and "clinic" matched
    "urgent_care_clinic", qualifying gift shops and urgent-care clinics as social media
    agencies. A named industry's niche categories must stand alone."""
    llm = FakeLLM('["food_restaurant", "pharmacy_health", "general_retail"]')
    t = await derive_discovery_targets("social media management and reporting software",
                                       industries=["social media agencies"], llm=llm)
    assert "office=advertising_agency" in t.osm_categories
    for leaked in ("restaurant", "cafe", "clinic", "doctor", "store", "retail", "shop"):
        assert leaked not in t.overture_categories, f"LLM buyer-sector guess leaked: {leaked}"
    for leaked in ("food_restaurant", "pharmacy_health", "general_retail"):
        assert leaked not in t.sectors, f"LLM buyer-sector guess leaked into sectors: {leaked}"


async def test_incidental_general_retail_match_does_not_block_niche_fallback():
    """Regression (2026-10-13 Virginia run, caught in the first log line). general_retail's match
    terms include "brand", so the offer phrase "one branded report" made match_sectors return
    ["general_retail"]. That counted as a deterministic match, which (a) skipped the niche
    fallback that would have produced office=advertising_agency and (b) let the LLM's buyer-sector
    guesses through, so the campaign's real targets became
    general_retail + hospitality + education + clothing_apparel + electronics - i.e. shop=*,
    hotel, lodging, school, store, retail. Every storefront in Virginia."""
    llm = FakeLLM('["general_retail", "hospitality", "education", "clothing_apparel"]')
    t = await derive_discovery_targets(
        "We sell a white-label client reporting dashboard that pulls metrics from every social "
        "platform into one branded report.",
        industries=["social media marketing agencies"], llm=llm)
    assert "office=advertising_agency" in t.osm_categories, t.osm_categories
    assert "general_retail" not in t.sectors, t.sectors
    for leaked in ("hospitality", "education", "clothing_apparel"):
        assert leaked not in t.sectors, f"LLM buyer-sector guess leaked into sectors: {leaked}"
    for leaked in ("store", "retail", "shop", "hotel", "lodging", "school"):
        assert leaked not in t.overture_categories, f"broad category leaked: {leaked}"
    assert "shop=*" not in t.osm_categories


def test_fallback_keywords_drop_function_words():
    """Regression (2026-10-13 Virginia run): the offer-derived relevance keywords came out as
    ['white', 'label', 'client', 'reporting', 'analytics', 'dashboard', 'pulls', 'metrics',
    'from', 'every', 'social', 'into']. These are reused as substring matchers against company
    names in the discovery relevance gate, where "from"/"every"/"into" are pure noise."""
    from gtm_engine.llm.tasks import _fallback_keywords

    kws = _fallback_keywords(
        "a dashboard that pulls metrics from every social platform into one branded report", None)
    for noise in ("from", "every", "into", "that", "one"):
        assert noise not in kws, f"function word kept as a relevance keyword: {noise}"
    assert "metrics" in kws and "dashboard" in kws


def test_pharmacy_health_overture_is_specific_not_broad():
    """`medical`/`health` LIKE-matched research institutes and generic health orgs into
    doctor/pharmacy searches. The sector must use specific substrings only."""
    tax = load_taxonomy()
    overture = tax["pharmacy_health"]["overture"]
    assert "medical" not in overture and "health" not in overture
    assert "pharmacy" in overture and "clinic" in overture and "doctor" in overture


async def test_derive_targets_pharmacy_offer_has_no_broad_medical_substring():
    t = await derive_discovery_targets("point-of-sale software for pharmacies and clinics",
                                       industries=["pharmacies", "clinics"])
    assert "pharmacy_health" in t.sectors
    assert "medical" not in t.overture_categories and "health" not in t.overture_categories


async def test_derive_targets_grocery_still_matches_taxonomy():
    t = await derive_discovery_targets("Find grocery stores in Islamabad",
                                       industries=["grocery stores"])
    assert "grocery_supermarket" in t.sectors
    assert "shop=supermarket" in t.osm_categories


async def test_seed_queries_include_areas():
    t = await derive_discovery_targets("Find newspaper offices near G-7 Islamabad",
                                       industries=["newspaper offices"],
                                       cities=["Islamabad"], areas=["G-7"])
    assert any("G-7" in q for q in t.search_queries)
