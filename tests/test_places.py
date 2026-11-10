"""Tests for Google Places API enrichment module."""

import json

import pytest
import httpx
import respx

from gtm_engine.enrichment.places import (
    PlacesData,
    _text_search,
    _place_details,
    places_enrichment,
)


# ── helpers ──────────────────────────────────────────────────────────────

class FakeFetcher:
    """Minimal fetcher that returns pre-configured responses."""

    def __init__(self, responses: dict[str, tuple[int, str]]):
        self._responses = responses

    async def get(self, url: str, **kwargs):
        for pattern, (status, body) in self._responses.items():
            if pattern in url:
                return _FakeResult(url, status, body)
        return _FakeResult(url, 404, "")

    async def close(self):
        pass


class _FakeResult:
    def __init__(self, url: str, status_code: int, text: str):
        self.url = url
        self.final_url = url
        self.status_code = status_code
        self.text = text
        self.content_type = "application/json"
        self.error = None if 200 <= status_code < 300 else "error"

    @property
    def ok(self):
        return self.error is None and 200 <= self.status_code < 300


# ── sample API responses ─────────────────────────────────────────────────

TEXT_SEARCH_RESPONSE = json.dumps({
    "places": [{"id": "ChIJ_example123"}]
})

TEXT_SEARCH_EMPTY = json.dumps({"places": []})

PLACE_DETAILS_RESPONSE = json.dumps({
    "rating": 4.3,
    "userRatingCount": 127,
    "regularOpeningHours": {
        "periods": [
            {"open": {"day": 1, "hour": 9, "minute": 0},
             "close": {"day": 1, "hour": 18, "minute": 0}},
        ],
        "weekdayDescriptions": ["Monday: 9:00 AM – 6:00 PM"],
    },
    "currentOpeningHours": {"openNow": True},
    "websiteUri": "https://example.pk",
})

PLACE_DETAILS_MINIMAL = json.dumps({
    "rating": 3.5,
    "userRatingCount": 12,
})


# ── text search tests ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_text_search_finds_place_id():
    with respx.mock:
        respx.post("https://places.googleapis.com/v1/places:searchText").mock(
            return_value=httpx.Response(200, json={"places": [{"id": "ChIJ_abc"}]})
        )
        result = await _text_search("fake-key", "Test Store, Islamabad")
        assert result == "ChIJ_abc"


@pytest.mark.asyncio
async def test_text_search_empty_results():
    with respx.mock:
        respx.post("https://places.googleapis.com/v1/places:searchText").mock(
            return_value=httpx.Response(200, json={"places": []})
        )
        result = await _text_search("fake-key", "Nonexistent Store")
        assert result is None


@pytest.mark.asyncio
async def test_text_search_api_error():
    with respx.mock:
        respx.post("https://places.googleapis.com/v1/places:searchText").mock(
            return_value=httpx.Response(403, text="Forbidden")
        )
        result = await _text_search("bad-key", "Store")
        assert result is None


@pytest.mark.asyncio
async def test_text_search_network_error():
    with respx.mock:
        respx.post("https://places.googleapis.com/v1/places:searchText").mock(
            side_effect=httpx.ConnectError("connection refused")
        )
        result = await _text_search("fake-key", "Store")
        assert result is None


# ── place details tests ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_place_details_full():
    fetcher = FakeFetcher({"ChIJ_example123": (200, PLACE_DETAILS_RESPONSE)})
    result = await _place_details(fetcher, "ChIJ_example123", "fake-key")
    assert result is not None
    assert result.place_id == "ChIJ_example123"
    assert result.rating == 4.3
    assert result.user_rating_count == 127
    assert result.is_open_now is True
    assert result.website_from_google == "https://example.pk"
    assert result.opening_hours is not None


@pytest.mark.asyncio
async def test_place_details_minimal():
    fetcher = FakeFetcher({"ChIJ_min": (200, PLACE_DETAILS_MINIMAL)})
    result = await _place_details(fetcher, "ChIJ_min", "fake-key")
    assert result is not None
    assert result.rating == 3.5
    assert result.user_rating_count == 12
    assert result.is_open_now is None
    assert result.opening_hours is None


@pytest.mark.asyncio
async def test_place_details_not_found():
    fetcher = FakeFetcher({})
    result = await _place_details(fetcher, "ChIJ_nonexistent", "fake-key")
    assert result is None


@pytest.mark.asyncio
async def test_place_details_bad_json():
    fetcher = FakeFetcher({"ChIJ_bad": (200, "not json at all")})
    result = await _place_details(fetcher, "ChIJ_bad", "fake-key")
    assert result is None


# ── end-to-end enrichment ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_places_enrichment_full():
    fetcher = FakeFetcher({"ChIJ_abc": (200, PLACE_DETAILS_RESPONSE)})
    with respx.mock:
        respx.post("https://places.googleapis.com/v1/places:searchText").mock(
            return_value=httpx.Response(200, json={"places": [{"id": "ChIJ_abc"}]})
        )
        result = await places_enrichment(fetcher, "Test Store", "Islamabad", "Pakistan", "fake-key")
    assert result is not None
    assert result.place_id == "ChIJ_abc"
    assert result.rating == 4.3


@pytest.mark.asyncio
async def test_places_enrichment_no_match():
    fetcher = FakeFetcher({})
    with respx.mock:
        respx.post("https://places.googleapis.com/v1/places:searchText").mock(
            return_value=httpx.Response(200, json={"places": []})
        )
        result = await places_enrichment(fetcher, "Ghost Store", "Nowhere", "Pakistan", "fake-key")
    assert result is None


# ── gap score integration ────────────────────────────────────────────────

def test_google_reviews_reduce_gap():
    from gtm_engine.enrichment.online_presence import _compute_online_gap
    from gtm_engine.models import OnlinePresence

    op_no_reviews = OnlinePresence()
    op_many_reviews = OnlinePresence(google_review_count=100, google_place_id="ChIJ_x")

    gap_no = _compute_online_gap(op_no_reviews)
    gap_many = _compute_online_gap(op_many_reviews)
    assert gap_many < gap_no


def test_low_google_visibility_label():
    from gtm_engine.enrichment.online_presence import online_gap_labels
    from gtm_engine.models import OnlinePresence

    op = OnlinePresence(google_place_id="ChIJ_x", google_review_count=3)
    labels = online_gap_labels(op)
    assert "low_google_visibility" in labels

    op_high = OnlinePresence(google_place_id="ChIJ_x", google_review_count=500)
    labels_high = online_gap_labels(op_high)
    assert "low_google_visibility" not in labels_high


def test_no_google_data_no_label():
    from gtm_engine.enrichment.online_presence import online_gap_labels
    from gtm_engine.models import OnlinePresence

    op = OnlinePresence()
    labels = online_gap_labels(op)
    assert "low_google_visibility" not in labels
