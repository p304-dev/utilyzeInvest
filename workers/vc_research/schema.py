"""Strict JSON contract the vc_research LLM call must satisfy.

Mirrors the Investors tab's worker-writable columns (D–O) plus the
worker's own assessment fields. Personal contact names are deliberately
absent: the sheet no longer has First/Last columns, and firm-level data
is all that gets published downstream.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class VCResearchResult(BaseModel):
    investor_name: str
    website: str | None = None
    industry_focus: str | None = None
    stage: str | None = None
    email: str | None = None
    phone: str | None = None
    city: str | None = None
    state_or_country: str | None = None
    deadline: str | None = None
    application_link: str | None = None
    linkedin_url: str | None = None
    twitter_url: str | None = None
    newsletter: str | None = None
    has_contact_form: bool
    draft_subject: str
    draft_body: str
    confidence: float = Field(ge=0.0, le=1.0)
    source_urls: list[str] = Field(default_factory=list)
    notes: str | None = None
