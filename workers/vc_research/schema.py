"""Strict JSON contract the vc_research LLM call must satisfy.

Field names and shapes follow the VC Research Worker build spec, Section 5,
exactly. Every field the prompt asks for is represented here — nothing
more, nothing less.
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
    contact_first_name: str | None = None
    contact_last_name: str | None = None
    has_contact_form: bool
    draft_subject: str
    draft_body: str
    confidence: float = Field(ge=0.0, le=1.0)
    source_urls: list[str] = Field(default_factory=list)
    notes: str | None = None
