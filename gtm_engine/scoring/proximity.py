"""Proximity tier scoring: rank businesses by distance from the user's anchor location.

Tier 1 (closest) = full points, Tier 2 = mid, Tier 3 = far.
When no anchor is configured, all businesses get Tier 1 (same-city assumption).
"""

from __future__ import annotations

import math


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres between two points."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def proximity_tier(
    company_lat: float | None,
    company_lon: float | None,
    anchor_lat: float | None,
    anchor_lon: float | None,
    tier1_km: float = 5.0,
    tier2_km: float = 15.0,
) -> tuple[int, float | None]:
    """Returns (tier, distance_km). Tier: 1 (closest), 2, or 3 (farthest).
    Returns (1, None) when either point is missing (same-city default)."""
    if anchor_lat is None or anchor_lon is None or company_lat is None or company_lon is None:
        return 1, None
    dist = haversine_km(anchor_lat, anchor_lon, company_lat, company_lon)
    if dist <= tier1_km:
        return 1, dist
    if dist <= tier2_km:
        return 2, dist
    return 3, dist


TIER_POINTS = {1: 15, 2: 12, 3: 8}
