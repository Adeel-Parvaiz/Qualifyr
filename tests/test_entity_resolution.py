"""Entity resolution match tiers: LEI, registration number, domain, fuzzy legal name."""

from gtm_engine.models import DiscoveredCompany
from gtm_engine.validation.entity_resolution import normalize_legal_name, resolve_entities


def _company(**kwargs) -> DiscoveredCompany:
    kwargs.setdefault("source", "test")
    return DiscoveredCompany(**kwargs)


def test_normalize_legal_name_strips_wide_suffix_set():
    assert normalize_legal_name("XYZ (PRIVATE) LIMITED") == "xyz"
    assert normalize_legal_name("XYZ Pvt Ltd") == "xyz"
    assert normalize_legal_name("Acme GmbH") == "acme"
    assert normalize_legal_name("Acme Holdings Group") == "acme"


def test_lei_exact_match_merges_across_sources():
    a = _company(name="Acme Pvt Ltd", country="Pakistan", source="overture",
                 extra={"lei": "LEI123"})
    b = _company(name="Acme (Private) Limited", country="Pakistan", source="gleif",
                 extra={"lei": "LEI123"}, phone="042-111222")
    result = resolve_entities([a, b])
    assert len(result) == 1
    assert result[0].phone == "042-111222"
    assert "gleif" in result[0].source and "overture" in result[0].source


def test_registration_number_match_is_country_scoped():
    a = _company(name="Acme Ltd", country="Pakistan", source="secp",
                 extra={"registration_number": "REG1"})
    b = _company(name="Different Co", country="United Kingdom", source="companies_house",
                 extra={"registration_number": "REG1"})
    result = resolve_entities([a, b])
    assert len(result) == 2  # same reg number, different country -> never merged


def test_domain_exact_match_merges():
    a = _company(name="Acme Store", website="https://acme.pk", source="osm")
    b = _company(name="Acme Store Pvt Ltd", website="https://www.acme.pk/about", source="gleif")
    result = resolve_entities([a, b])
    assert len(result) == 1


def test_fuzzy_legal_name_match_same_country():
    a = _company(name="Bashir Sons Pharmacy", country="Pakistan", source="osm")
    b = _company(name="Bashir Sons Pharmacy (Private) Limited", country="Pakistan", source="secp",
                 extra={"registration_number": "REG9"})
    result = resolve_entities([a, b])
    assert len(result) == 1
    assert result[0].extra.get("registration_number") == "REG9"


def test_fuzzy_name_never_merges_across_countries():
    a = _company(name="Acme Trading Company", country="Pakistan", city="Lahore", source="osm")
    b = _company(name="Acme Trading Company", country="United Kingdom", city="London", source="wikidata")
    result = resolve_entities([a, b])
    assert len(result) == 2


def test_unrelated_companies_stay_separate():
    a = _company(name="Acme Pharmacy", country="Pakistan")
    b = _company(name="Zenith Clothing", country="Pakistan")
    result = resolve_entities([a, b])
    assert len(result) == 2
