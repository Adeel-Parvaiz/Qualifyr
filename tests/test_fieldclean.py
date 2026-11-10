"""Unit tests for human-shareable field cleaning (gtm_engine/enrichment/fieldclean.py).

Values here are taken from real exported CSVs that read badly, so each test pins a
concrete defect we fixed.
"""

from gtm_engine.enrichment.fieldclean import (
    clean_address,
    clean_description,
    clean_email,
    clean_reason,
    humanize_industry,
    looks_like_boilerplate,
    prefer_latin_name,
)


# -- e-mail ------------------------------------------------------------------

def test_clean_email_strips_url_encoding():
    assert clean_email("%20contact@metroev.pk") == "contact@metroev.pk"


def test_clean_email_strips_fused_phone_prefix():
    assert clean_email("03009502334info@khalisthings.com") == "info@khalisthings.com"


def test_clean_email_passes_clean_address():
    assert clean_email("info@mcc.com.pk") == "info@mcc.com.pk"


def test_clean_email_rejects_garbage():
    assert clean_email("not an email") is None
    assert clean_email("") is None
    assert clean_email(None) is None


# -- address -----------------------------------------------------------------

def test_clean_address_keeps_first_of_several():
    multi = "No. 20, Street 1, F-6/3, Islamabad\n\nNo. 05-06, Residencia One\n\nUnit 1, Melody Market"
    assert clean_address(multi) == "No. 20, Street 1, F-6/3, Islamabad"


def test_clean_address_collapses_whitespace():
    assert clean_address("  Service   Road   East  ") == "Service Road East"


def test_clean_address_empty():
    assert clean_address(None) is None
    assert clean_address("") is None


# -- company name ------------------------------------------------------------

def test_prefer_latin_name_swaps_non_latin_for_title():
    assert prefer_latin_name("گلوریا جینز", "Gloria Jean's Coffees - Home") == "Gloria Jean's Coffees"


def test_prefer_latin_name_keeps_latin():
    assert prefer_latin_name("Madina Cash & Carry", "whatever") == "Madina Cash & Carry"


# -- description / boilerplate ----------------------------------------------

def test_nav_menu_is_boilerplate():
    nav = "Home Our Story Our Menu Our Stores Partnering With Us Contact Us Home Our Story"
    assert looks_like_boilerplate(nav) is True


def test_skip_to_content_is_boilerplate():
    assert looks_like_boilerplate("Skip to content About Us Explore our Facility") is True


def test_real_sentence_is_not_boilerplate():
    assert looks_like_boilerplate("We are a 4-star hotel offering affordable luxury in Islamabad.") is False


def test_clean_description_falls_back_to_brief_when_meta_is_junk():
    nav = "Home Our Story Our Menu Our Stores Partnering With Us Contact Us"
    brief = "Gloria Jeans is a premium coffee chain based in Australia."
    assert clean_description(nav, brief) == brief


def test_clean_description_keeps_good_meta():
    meta = "Discover aesthetic care at Cozmetika Clinic Islamabad with personalized treatments."
    assert clean_description(meta, None) == meta


def test_clean_description_empty_when_nothing_usable():
    assert clean_description(None, None) is None
    assert clean_description("Home Menu Cart Login Account About Contact", None) is None


# -- category / reason -------------------------------------------------------

def test_humanize_industry():
    assert humanize_industry("overture=health_care") == "Health Care"
    assert humanize_industry("shop=supermarket") == "Supermarket"
    assert humanize_industry(None) == ""


def test_clean_reason_drops_internal_notes():
    r = ("518 Google reviews (200+); Google rating 4.8/5; "
         "Tier 1 (no anchor configured, same-city default); "
         "online gap: no ordering channel; classified as BUYER")
    out = clean_reason(r)
    assert "Tier 1" not in out
    assert "classified as" not in out
    assert "Google rating 4.8/5" in out
    assert "online gap: no ordering channel" in out
