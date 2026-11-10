"""Tests for the online-presence / digital-maturity audit and online-gap scoring."""

from gtm_engine.enrichment.online_presence import audit_online_presence, _compute_online_gap, online_gap_labels
from gtm_engine.models import (
    Classification, CompanyQuality, CompanyType, Contact, DiscoveredCompany,
    EmailStatus, OnlinePresence, Priority, Signals,
)
from gtm_engine.scoring.scoring import ScoreInputs, score_lead
from gtm_engine.scraping.parsers import parse_page
from gtm_engine.scraping.site_crawler import SiteSnapshot


# ── helpers ──────────────────────────────────────────────────────────────

def _snapshot_with_html(html: str, reachable: bool = True) -> SiteSnapshot:
    snap = SiteSnapshot(website="https://example.pk", final_url="https://example.pk/", reachable=reachable, https=True)
    if reachable:
        snap.pages["home"] = parse_page("https://example.pk/", html)
        snap.raw_html["home"] = html
    return snap


def _company(**kw) -> DiscoveredCompany:
    base = dict(name="Test Store", website="https://example.pk", city="Islamabad", country="Pakistan", source="osm")
    base.update(kw)
    return DiscoveredCompany(**base)


# ── e-commerce detection ────────────────────────────────────────────────

def test_shopify_site_detected():
    html = '<html><body><script src="https://cdn.shopify.com/s/files/1/theme.js"></script>'
    html += '<button class="add-to-cart">Add to Cart</button></body></html>'
    op = audit_online_presence(_snapshot_with_html(html))
    assert op.has_ecommerce_site
    assert op.has_cart
    assert op.ecommerce_platform == "shopify"
    assert op.online_gap_score <= 10


def test_woocommerce_site_detected():
    html = '<html><body><div class="woocommerce"><a class="wc-add-to-cart">Buy Now</a></div></body></html>'
    op = audit_online_presence(_snapshot_with_html(html))
    assert op.has_ecommerce_site
    assert op.has_cart
    assert op.ecommerce_platform == "woocommerce"


def test_no_ecommerce_on_plain_site():
    html = '<html><body><h1>Welcome to our store</h1><p>Visit us at F-11 Markaz</p></body></html>'
    op = audit_online_presence(_snapshot_with_html(html))
    assert not op.has_ecommerce_site
    assert not op.has_cart
    assert op.ecommerce_platform is None
    assert op.online_gap_score >= 18


def test_ecommerce_from_technology_markers():
    html = '<html><body><h1>Our Shop</h1></body></html>'
    op = audit_online_presence(_snapshot_with_html(html), technologies=["shopify"])
    assert op.has_ecommerce_site
    assert op.ecommerce_platform == "shopify"


# ── WhatsApp detection ──────────────────────────────────────────────────

def test_whatsapp_link_detected():
    html = '<html><body><a href="https://wa.me/923001234567">Order on WhatsApp</a></body></html>'
    op = audit_online_presence(_snapshot_with_html(html))
    assert op.has_whatsapp_ordering
    assert op.whatsapp_number == "923001234567"


def test_whatsapp_api_link_detected():
    html = '<html><body><a href="https://api.whatsapp.com/send?phone=923001234567">Chat</a></body></html>'
    op = audit_online_presence(_snapshot_with_html(html))
    assert op.has_whatsapp_ordering
    assert op.whatsapp_number == "923001234567"


def test_whatsapp_plus_prefix_detected():
    html = '<html><body><a href="https://api.whatsapp.com/send?phone=+923001234567&text=hi">Order</a></body></html>'
    op = audit_online_presence(_snapshot_with_html(html))
    assert op.has_whatsapp_ordering
    assert op.whatsapp_number == "923001234567"


def test_no_whatsapp_on_plain_site():
    html = '<html><body><p>Call us at 051-1234567</p></body></html>'
    op = audit_online_presence(_snapshot_with_html(html))
    assert not op.has_whatsapp_ordering
    assert op.whatsapp_number is None


# ── social detection ────────────────────────────────────────────────────

def test_facebook_and_instagram_detected():
    html = '''<html><body>
    <a href="https://www.facebook.com/teststore">Facebook</a>
    <a href="https://www.instagram.com/teststore">Instagram</a>
    </body></html>'''
    op = audit_online_presence(_snapshot_with_html(html))
    assert op.has_facebook
    assert op.has_instagram
    assert "facebook.com" in op.facebook_url
    assert "instagram.com" in op.instagram_url


# ── delivery platform detection ─────────────────────────────────────────

def test_foodpanda_link_detected():
    html = '<html><body><p>Also find us on <a href="https://foodpanda.pk/store/test">Foodpanda</a></p></body></html>'
    op = audit_online_presence(_snapshot_with_html(html))
    assert "foodpanda" in op.delivery_platforms


def test_multiple_delivery_platforms():
    html = '<html><body><p>Order on foodpanda.pk or daraz.pk or bykea.com</p></body></html>'
    op = audit_online_presence(_snapshot_with_html(html))
    assert "foodpanda" in op.delivery_platforms
    assert "daraz" in op.delivery_platforms
    assert "bykea" in op.delivery_platforms


def test_pakistani_platform_not_claimed_as_gap_for_spain():
    """A Spanish company using Glovo (a real international platform) must be detected; it must
    never be penalised for lacking Daraz/Foodpanda, which don't operate in Spain - checking for
    a platform that structurally cannot exist there isn't a real online-gap signal."""
    html = '<html><body><p>Pide ya en glovo.es</p></body></html>'
    op = audit_online_presence(_snapshot_with_html(html), country="Spain")
    assert "glovo" in op.delivery_platforms
    assert "foodpanda" not in op.delivery_platforms
    assert "daraz" not in op.delivery_platforms


def test_global_platform_detected_regardless_of_country():
    html = '<html><body><p>Order via ubereats.com</p></body></html>'
    op_pk = audit_online_presence(_snapshot_with_html(html), country="Pakistan")
    op_es = audit_online_presence(_snapshot_with_html(html), country="Spain")
    assert "ubereats" in op_pk.delivery_platforms
    assert "ubereats" in op_es.delivery_platforms


# ── delivery model inference ────────────────────────────────────────────

def test_own_delivery_inferred():
    html = '<html><body><button class="add-to-cart">Buy</button><p>We deliver to your doorstep.</p></body></html>'
    op = audit_online_presence(_snapshot_with_html(html))
    assert op.delivery_model == "own"


def test_third_party_only():
    html = '<html><body><p>Find us on foodpanda.pk for delivery.</p></body></html>'
    op = audit_online_presence(_snapshot_with_html(html))
    assert op.delivery_model == "third_party"


def test_phone_only_delivery():
    html = '<html><body><a href="https://wa.me/923001234567">Order</a><p>No website ordering.</p></body></html>'
    op = audit_online_presence(_snapshot_with_html(html))
    assert op.delivery_model == "phone_only"


def test_no_delivery():
    html = '<html><body><h1>Our Store</h1><p>Visit us in F-11.</p></body></html>'
    op = audit_online_presence(_snapshot_with_html(html))
    assert op.delivery_model == "none"


# ── online gap score ────────────────────────────────────────────────────

def test_max_gap_for_bare_business():
    op = OnlinePresence()
    assert _compute_online_gap(op) == 22


def test_ecommerce_reduces_gap():
    op = OnlinePresence(has_ecommerce_site=True, has_cart=True)
    assert _compute_online_gap(op) == 22 - 12


def test_full_digital_presence_minimal_gap():
    op = OnlinePresence(
        has_ecommerce_site=True, has_cart=True, has_mobile_app=True,
        delivery_platforms=["foodpanda", "daraz", "bykea"],
        has_whatsapp_ordering=True, has_facebook=True, has_instagram=True,
    )
    assert _compute_online_gap(op) == 0


# ── unreachable site ────────────────────────────────────────────────────

def test_unreachable_site_defaults():
    snap = _snapshot_with_html("", reachable=False)
    op = audit_online_presence(snap)
    assert not op.has_ecommerce_site
    assert op.delivery_model == "unknown"
    assert any("unreachable" in n for n in op.notes)


# ── scoring integration ─────────────────────────────────────────────────

def test_online_gap_affects_total_score(campaign):
    from gtm_engine.config.schema import CampaignConfig, GeographyConfig
    campaign = CampaignConfig(
        campaign_id="test", name="test", offer="test",
        geography=GeographyConfig(countries=["Pakistan"], cities=["Islamabad"]),
        buyer_keywords=["store"],
    )
    base_inputs = ScoreInputs(
        company=_company(),
        classification=Classification(company_type=CompanyType.BUYER, confidence=0.9, buyer_hits=["store"]),
        quality=CompanyQuality(reachable=True, https=True, has_contact_page=True, page_count=3),
        contact=Contact(name="Owner", role="CEO", email="x@example.pk", email_status=EmailStatus.MX_VALID, is_decision_maker=True),
        signals=Signals(buying={"hiring": ["yes"]}),
    )

    # No online presence → online_gap = 0 (absent, not scored)
    score_no_op = score_lead(base_inputs, campaign)

    # High gap (no digital channels)
    high_gap = OnlinePresence(online_gap_score=22)
    inputs_high = ScoreInputs(**{**base_inputs.__dict__, "online_presence": high_gap})
    score_high_gap = score_lead(inputs_high, campaign)

    # Low gap (full digital)
    low_gap = OnlinePresence(
        has_ecommerce_site=True, has_cart=True, has_mobile_app=True,
        delivery_platforms=["foodpanda"], online_gap_score=0,
    )
    inputs_low = ScoreInputs(**{**base_inputs.__dict__, "online_presence": low_gap})
    score_low_gap = score_lead(inputs_low, campaign)

    assert score_high_gap.online_gap > score_low_gap.online_gap
    assert score_high_gap.total > score_low_gap.total
    assert any("online gap" in r for r in score_high_gap.reasons)
    assert score_high_gap.total == (score_high_gap.review_band + score_high_gap.rating_score +
                                     score_high_gap.proximity_tier + score_high_gap.online_gap +
                                     score_high_gap.pain_evidence)


# ── gap labels ─────────────────────────────────────────────────────────

def test_bare_presence_has_all_gaps():
    labels = online_gap_labels(OnlinePresence())
    assert "no_ecommerce" in labels
    assert "no_whatsapp_ordering" in labels
    assert "no_delivery_platform" in labels
    assert "no_mobile_app" in labels
    assert "no_social" in labels


def test_full_presence_has_no_gaps():
    op = OnlinePresence(
        has_ecommerce_site=True, has_cart=True, has_mobile_app=True,
        has_whatsapp_ordering=True, has_facebook=True, has_instagram=True,
        delivery_platforms=["foodpanda"],
    )
    assert online_gap_labels(op) == []


def test_ecommerce_without_cart_shows_no_cart():
    op = OnlinePresence(has_ecommerce_site=True, has_cart=False)
    labels = online_gap_labels(op)
    assert "no_cart" in labels
    assert "no_ecommerce" not in labels


# ── pitch angle (deterministic fallback) ───────────────────────────────

async def test_pitch_fallback_no_llm():
    from gtm_engine.llm.tasks import generate_pitch_angle
    pitch = await generate_pitch_angle(
        None, "inventory software", "Test Store",
        online_gaps=["no_ecommerce", "no_mobile_app"],
        pain_signals=[], buying_signals=[],
    )
    assert "Test Store" in pitch
    assert "no online ordering" in pitch or "inventory software" in pitch


async def test_pitch_fallback_with_pain():
    from gtm_engine.llm.tasks import generate_pitch_angle
    pitch = await generate_pitch_angle(
        None, "delivery app", "Corner Shop",
        online_gaps=["no_delivery_platform"],
        pain_signals=["customer_service_load"],
        buying_signals=[],
    )
    assert "Corner Shop" in pitch
    assert "delivery" in pitch.lower() or "pain" in pitch.lower()


async def test_pitch_fallback_no_gaps_no_signals():
    from gtm_engine.llm.tasks import generate_pitch_angle
    pitch = await generate_pitch_angle(
        None, "POS system", "Big Mart",
        online_gaps=[], pain_signals=[], buying_signals=[],
    )
    assert "Big Mart" in pitch
    assert "POS system" in pitch
