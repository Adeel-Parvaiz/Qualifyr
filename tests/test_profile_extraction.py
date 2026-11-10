"""P5: attach a decision-maker's own LinkedIn profile, matched by name; never a stranger's."""

from gtm_engine.enrichment.contacts import _match_personal_profile
from gtm_engine.scraping.parsers import parse_page, personal_profiles


def test_personal_profiles_extracts_in_links_only():
    html = """<a href="https://www.linkedin.com/company/rakht">Rakht</a>
              <a href="https://linkedin.com/in/ahmed-raza-123">Ahmed</a>
              <a href="http://www.linkedin.com/in/sana.malik">Sana</a>"""
    profs = personal_profiles(html)
    assert profs == ["https://linkedin.com/in/ahmed-raza-123", "https://linkedin.com/in/sana.malik"]
    assert not any("/company/" in p for p in profs)


def test_parse_page_populates_profiles():
    page = parse_page("https://x.pk/team", '<a href="https://linkedin.com/in/ahmed-raza">A</a>')
    assert page.profiles == ["https://linkedin.com/in/ahmed-raza"]


def test_match_requires_both_names():
    profiles = ["https://linkedin.com/in/ahmed-raza-99", "https://linkedin.com/in/someone-else"]
    assert _match_personal_profile("Ahmed Raza", profiles) == "https://linkedin.com/in/ahmed-raza-99"
    # Only a surname match must NOT attach a stranger's profile.
    assert _match_personal_profile("Bilal Raza", ["https://linkedin.com/in/ahmed-raza-99"]) is None
    assert _match_personal_profile("Ahmed", profiles) is None      # single name: no confident match
    assert _match_personal_profile("Ahmed Raza", []) is None
