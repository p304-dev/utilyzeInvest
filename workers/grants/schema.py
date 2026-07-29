"""Strict JSON contract the grants LLM call must satisfy. Field names and
shapes follow the Grants Worker build spec, Section 4, exactly.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Eligibility = Literal["Eligible", "Not eligible", "Unclear"]
RecommendedAction = Literal["Apply", "Watch", "Skip"]


class GrantsResult(BaseModel):
    name: str
    category: str | None = None
    eligible: Eligibility
    eligibility_reason: str | None = None
    deadline: str | None = None
    contact_email: str | None = None
    application_link: str | None = None
    requirements: list[str] = Field(default_factory=list)
    fit_score: int = Field(ge=0, le=100)
    fit_rationale: str
    recommended_action: RecommendedAction
    draft_outline: str
    confidence: float = Field(ge=0.0, le=1.0)
    source_urls: list[str] = Field(default_factory=list)
    notes: str | None = None
