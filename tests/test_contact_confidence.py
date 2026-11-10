"""Contact-layer improvements (PRICING_PHASES_2.md "turn up the finder" items that don't
need Reacher): single-column contact_confidence score, catch-all surfaced on Lead, and the
discover() helper reporting catch_all back to the caller."""

import pytest

from gtm_engine.enrichment.email_patterns import discover
from gtm_engine.models import EmailStatus, score_contact_confidence
from gtm_engine.validation.verifier import VerifyResult, VerifyStatus


# --- contact_confidence scoring ---------------------------------------------------------


def test_confidence_deliverable_is_100():
    assert score_contact_confidence(EmailStatus.DELIVERABLE, False, False, False) == 100


def test_confidence_catch_all_collapses_mx_valid():
    # An MX-valid address on a catch-all domain is nearly worthless; floor it.
    assert score_contact_confidence(EmailStatus.MX_VALID, True, False, False) <= 15


def test_confidence_catch_all_leaves_deliverable_alone():
    assert score_contact_confidence(EmailStatus.DELIVERABLE, True, False, False) == 100


def test_confidence_candidate_present_lifts_a_weak_generic():
    # generic alone = 30; with a candidate_email in hand it should at least match 20 floor
    # (we care that a reviewer sees "there's a guess").
    with_cand = score_contact_confidence(EmailStatus.GENERIC, False, True, False)
    without = score_contact_confidence(EmailStatus.GENERIC, False, False, False)
    assert with_cand >= without


def test_confidence_phone_bonus_small_and_capped():
    base = score_contact_confidence(EmailStatus.MX_VALID, False, False, False)
    boost = score_contact_confidence(EmailStatus.MX_VALID, False, False, True)
    assert 0 < boost - base <= 5
    assert score_contact_confidence(EmailStatus.DELIVERABLE, False, False, True) == 100  # no overflow


def test_confidence_zero_for_dead_ends():
    for s in (EmailStatus.NONE, EmailStatus.INVALID, EmailStatus.BOUNCED):
        assert score_contact_confidence(s, False, False, False) == 0


# --- discover() reports catch_all back ---------------------------------------------------


class _CatchAllVerifier:
    name = "scripted"

    async def verify(self, email):
        return VerifyResult(VerifyStatus.INVALID, self.name, "unused")

    async def is_catch_all(self, domain):
        return True


class _CleanVerifier:
    name = "scripted"

    async def verify(self, email):
        return VerifyResult(VerifyStatus.DELIVERABLE, self.name, "250 ok")

    async def is_catch_all(self, domain):
        return False


async def test_discover_marks_catch_all_true_when_domain_accepts_everything():
    found = await discover("Ahmed Raza", "wideopen.pk", _CatchAllVerifier())
    assert found.catch_all is True
    assert found.status == VerifyStatus.RISKY


async def test_discover_catch_all_is_none_or_false_on_clean_domain():
    found = await discover("Ahmed Raza", "zarafabrics.pk", _CleanVerifier())
    assert not found.catch_all   # None or False both fine
