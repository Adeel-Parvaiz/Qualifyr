"""Google Places API (New) enrichment: rating, review count, opening hours.

Free tier (per month, no billing charge):
  - Text Search IDs Only: unlimited
  - Place Details Enterprise: 1,000 calls (rating, hours, review count)

Requires GTM_GOOGLE_PLACES_API_KEY and enable_places_enrichment=True.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

import httpx

from gtm_engine.scraping.fetcher import Fetcher

log = logging.getLogger(__name__)

TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
PLACE_DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"

DETAILS_FIELD_MASK = (
    "rating,userRatingCount,regularOpeningHours,currentOpeningHours,websiteUri"
)

DETAILS_FIELD_MASK_WITH_REVIEWS = (
    "rating,userRatingCount,regularOpeningHours,currentOpeningHours,websiteUri,reviews"
)


@dataclass
class PlacesData:
    place_id: str
    rating: float | None = None
    user_rating_count: int | None = None
    opening_hours: dict | None = None
    is_open_now: bool | None = None
    website_from_google: str | None = None
    review_texts: list[str] = field(default_factory=list)


async def _text_search(api_key: str, query: str) -> str | None:
    """Find a Place ID via Text Search (IDs Only SKU = unlimited free)."""
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "places.id",
    }
    body = {"textQuery": query}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            resp = await client.post(TEXT_SEARCH_URL, json=body, headers=headers)
        if resp.status_code != 200:
            log.debug("places text-search %d for %r", resp.status_code, query)
            return None
        data = resp.json()
        places = data.get("places", [])
        if not places:
            return None
        return places[0].get("id")
    except (httpx.HTTPError, json.JSONDecodeError, KeyError) as exc:
        log.debug("places text-search error for %r: %s", query, exc)
        return None


async def _place_details(fetcher: Fetcher, place_id: str, api_key: str,
                         include_reviews: bool = False) -> PlacesData | None:
    """Fetch Place Details (Enterprise SKU: 1K free/month; +Atmosphere for reviews)."""
    url = PLACE_DETAILS_URL.format(place_id=place_id)
    mask = DETAILS_FIELD_MASK_WITH_REVIEWS if include_reviews else DETAILS_FIELD_MASK
    headers = {
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": mask,
    }
    result = await fetcher.get(url, api=True, headers=headers)
    if not result.ok:
        log.debug("places details %d for %s", result.status_code, place_id)
        return None
    try:
        data = json.loads(result.text)
    except json.JSONDecodeError:
        return None

    hours_data = data.get("regularOpeningHours")
    current_hours = data.get("currentOpeningHours")

    review_texts: list[str] = []
    for review in data.get("reviews", []):
        text = (review.get("text") or {}).get("text", "").strip()
        if text:
            review_texts.append(text)

    return PlacesData(
        place_id=place_id,
        rating=data.get("rating"),
        user_rating_count=data.get("userRatingCount"),
        opening_hours=hours_data,
        is_open_now=current_hours.get("openNow") if current_hours else None,
        website_from_google=data.get("websiteUri"),
        review_texts=review_texts,
    )


async def places_enrichment(
    fetcher: Fetcher,
    business_name: str,
    city: str,
    country: str,
    api_key: str,
    include_reviews: bool = False,
) -> PlacesData | None:
    """Two-step lookup: Text Search (free) -> Place Details (1K/month free)."""
    query = f"{business_name}, {city}, {country}"
    place_id = await _text_search(api_key, query)
    if not place_id:
        return None
    return await _place_details(fetcher, place_id, api_key, include_reviews=include_reviews)
