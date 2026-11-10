"""Common interface every discovery source implements, so the pipeline can add or
remove sources without changing."""

from __future__ import annotations

from typing import AsyncIterator, Protocol

from gtm_engine.config.schema import CampaignConfig
from gtm_engine.models import DiscoveredCompany


class DiscoverySource(Protocol):
    name: str

    def discover(self, campaign: CampaignConfig) -> AsyncIterator[DiscoveredCompany]: ...
