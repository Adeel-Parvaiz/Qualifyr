import pytest

from gtm_engine.models import DiscoveredCompany, EmailStatus
from gtm_engine.validation.dedupe import dedupe_companies, normalize_name
from gtm_engine.validation.domains import canonical_domain, company_key, is_social_url, normalize_url
from gtm_engine.validation.emails import classify_email, extract_emails, is_generic_mailbox, is_syntax_valid


# --- domains -----------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("https://www.Shop.com.pk/about?x=1", "shop.com.pk"),
    ("zarafabrics.pk", "zarafabrics.pk"),
    ("http://sub.example.co.uk/path", "example.co.uk"),
    ("https://www.facebook.com/somepage", None),
    ("mailto:x@y.com", None),
    ("not a url", None),
    ("", None),
    (None, None),
])
def test_canonical_domain(raw, expected):
    assert canonical_domain(raw) == expected


def test_normalize_url_adds_scheme():
    assert normalize_url("example.pk") == "https://example.pk/"
    assert normalize_url("HTTP://Example.PK/About#frag") == "http://example.pk/About"


def test_social_detection():
    assert is_social_url("https://instagram.com/brand")
    assert not is_social_url("https://brand.pk")


def test_company_key_prefers_domain():
    assert company_key("brand.pk", "Brand Store", "Lahore") == "brand.pk"
    assert company_key(None, "Brand Store!", "Lahore") == "brand-store|lahore"


# --- emails ------------------------------------------------------------------

def test_extract_emails_filters_junk():
    text = "Contact info@shop.pk or Sales@Shop.PK; image logo@2x.png; user@example.com"
    assert extract_emails(text) == ["info@shop.pk", "sales@shop.pk"]


@pytest.mark.parametrize("email,valid", [
    ("ahmed.raza@zarafabrics.pk", True),
    ("bad@@x.com", False),
    ("nodomain@", False),
    ("a..b@shop.pk", False),
    ("x@facebook.com", False),  # social host is not a business domain
])
def test_syntax(email, valid):
    assert is_syntax_valid(email) is valid


def test_generic_mailbox(defaults):
    assert is_generic_mailbox("info@shop.pk", defaults.generic_email_prefixes)
    assert not is_generic_mailbox("ahmed@shop.pk", defaults.generic_email_prefixes)


class FakeMX:
    def __init__(self, ok: set[str]):
        self.ok = ok

    async def has_mx(self, domain: str) -> bool:
        return domain in self.ok


async def test_classify_email_paths(defaults):
    mx = FakeMX({"shop.pk"})
    g = defaults.generic_email_prefixes
    assert await classify_email(None, g, mx) == EmailStatus.NONE
    assert await classify_email("bad@@x", g, mx) == EmailStatus.INVALID
    assert await classify_email("a@nomx.pk", g, mx) == EmailStatus.INVALID
    assert await classify_email("info@shop.pk", g, mx) == EmailStatus.GENERIC
    assert await classify_email("ahmed@shop.pk", g, mx) == EmailStatus.MX_VALID
    assert await classify_email("ahmed@shop.pk", g, None) == EmailStatus.UNVERIFIED


# --- dedupe ------------------------------------------------------------------

def test_normalize_name_strips_legal_suffix():
    assert normalize_name("Zara Fabrics (Pvt) Ltd.") == "zara fabrics"
    assert normalize_name("AL-FATAH Electronics Company") == "al fatah electronics"


def test_dedupe_by_domain_and_name_city():
    rows = [
        DiscoveredCompany(name="Zara Fabrics", website="https://www.zarafabrics.pk/", city="Islamabad", source="osm"),
        DiscoveredCompany(name="Zara Fabrics Pvt Ltd", website="http://zarafabrics.pk/branch-2", city="Islamabad", source="osm", phone="051"),
        DiscoveredCompany(name="Zara Fabrics", city="Islamabad", source="csv_seed", email="info@zarafabrics.pk"),
        DiscoveredCompany(name="Zara Fabrics", city="Lahore", source="osm"),  # different city, no domain -> separate
        DiscoveredCompany(name="Mobile Hut", website="https://facebook.com/mobilehut", city="Islamabad", source="osm"),
    ]
    out = dedupe_companies(rows)
    names = [(c.name, c.city, c.domain) for c in out]
    assert names == [
        ("Zara Fabrics", "Islamabad", "zarafabrics.pk"),
        ("Zara Fabrics", "Lahore", None),
        ("Mobile Hut", "Islamabad", None),
    ]
    merged = out[0]
    assert merged.phone == "051" and merged.email == "info@zarafabrics.pk"
    assert "csv_seed" in merged.source
    assert out[2].website is None  # social link is not a website


# --- regressions from the first live run -------------------------------------------

def test_hosted_platform_keeps_subdomain():
    assert canonical_domain("https://malik-hitech.business.site/") == "malik-hitech.business.site"
    assert canonical_domain("https://shop.myshopify.com") == "shop.myshopify.com"
    assert canonical_domain("https://business.site") is None
    assert canonical_domain("https://www.wixsite.com") is None


def test_customer_care_is_generic(defaults):
    for e in ("customercare@bata.com", "contactus@outfitters.com.pk", "feedback@csd.gov.pk", "orders@x.pk"):
        assert is_generic_mailbox(e, defaults.generic_email_prefixes), e
