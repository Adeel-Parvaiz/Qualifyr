from __future__ import annotations

from pydantic import BaseModel, Field


class IntentSignal(BaseModel):
    kind: str                      # tender | rfq | hiring | procurement_page | expansion
    source: str                    # ppra | website | ...
    source_url: str | None = None
    text: str                      # the requirement as published, trimmed
    organization: str | None = None
    date: str | None = None        # ISO date the signal was published, when known
    deadline: str | None = None    # ISO date it closes, when known
    matched_terms: list[str] = Field(default_factory=list)
    extracted: dict | None = None  # optional structured requirement (deterministic or llm:)
