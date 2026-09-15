"""Strict JSON contract the vc_research LLM call must satisfy.

Mirrors the Investors tab's worker-writable columns (D–O) plus the
worker's own assessment fields. Personal contact names are deliberately
absent: the sheet no longer has First/Last columns, and firm-level data
is all that gets published downstream.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

# `Industry Focus` — which sector a firm invests in. Not a business taste
# call — Utilyze only wants these five buckets, with "regular tech and
# stuff" deliberately funneled into the Generalist catch-all rather than
# the model inventing finer-grained sectors of its own.
SECTOR_GENERALIST = "Generalist"
SECTOR_CLIMATE = "Climate"
SECTOR_BIOTECH = "Biotech"
SECTOR_UTILITIES = "Utilities"
SECTOR_WATER = "Water"
SECTORS: tuple[str, ...] = (
    SECTOR_GENERALIST,
    SECTOR_CLIMATE,
    SECTOR_BIOTECH,
    SECTOR_UTILITIES,
    SECTOR_WATER,
)

# A handful of obvious phrasing variants the model might return despite the
# prompt's instructions — normalized rather than trusted verbatim so a
# stray "bio-tech" or "Bio Tech" doesn't slip an off-list value into a
# column meant to hold exactly one of five values. Anything unrecognized
# falls back to Generalist, the documented catch-all.
_SECTOR_ALIASES: dict[str, str] = {
    "generalist": SECTOR_GENERALIST,
    "general": SECTOR_GENERALIST,
    "tech": SECTOR_GENERALIST,
    "climate": SECTOR_CLIMATE,
    "climate tech": SECTOR_CLIMATE,
    "climatetech": SECTOR_CLIMATE,
    "cleantech": SECTOR_CLIMATE,
    "clean tech": SECTOR_CLIMATE,
    "biotech": SECTOR_BIOTECH,
    "bio-tech": SECTOR_BIOTECH,
    "bio tech": SECTOR_BIOTECH,
    "life sciences": SECTOR_BIOTECH,
    "utilities": SECTOR_UTILITIES,
    "utility": SECTOR_UTILITIES,
    "water": SECTOR_WATER,
    "water tech": SECTOR_WATER,
    "watertech": SECTOR_WATER,
}

# `Category` — what *kind* of opportunity the row even is, now that Grants
# / Pitches / Research rows live on the same Investors tab as VC firms.
# This is a different question from Industry Focus (what sector) and from
# Stage (what round) — every row gets exactly one of these five.
CATEGORY_INVESTOR = "Investor"
CATEGORY_ACCELERATOR = "Accelerator"
CATEGORY_GRANT = "Grant"
CATEGORY_PITCH = "Pitch"
CATEGORY_RESEARCH = "Research"
CATEGORIES: tuple[str, ...] = (
    CATEGORY_INVESTOR,
    CATEGORY_ACCELERATOR,
    CATEGORY_GRANT,
    CATEGORY_PITCH,
    CATEGORY_RESEARCH,
)

# Same normalize-don't-trust approach as sectors above. Unrecognized falls
# back to Investor — this tab's original, still-dominant row type.
_CATEGORY_ALIASES: dict[str, str] = {
    "investor": CATEGORY_INVESTOR,
    "vc": CATEGORY_INVESTOR,
    "venture capital": CATEGORY_INVESTOR,
    "venture capital firm": CATEGORY_INVESTOR,
    "fund": CATEGORY_INVESTOR,
    "accelerator": CATEGORY_ACCELERATOR,
    "accelerator program": CATEGORY_ACCELERATOR,
    "incubator": CATEGORY_ACCELERATOR,
    "grant": CATEGORY_GRANT,
    "grant program": CATEGORY_GRANT,
    "funding grant": CATEGORY_GRANT,
    "pitch": CATEGORY_PITCH,
    "pitch competition": CATEGORY_PITCH,
    "pitch comp": CATEGORY_PITCH,
    "pitch event": CATEGORY_PITCH,
    "competition": CATEGORY_PITCH,
    "research": CATEGORY_RESEARCH,
    "research topic": CATEGORY_RESEARCH,
    "topic": CATEGORY_RESEARCH,
}

_ALPHA_ONLY = re.compile(r"[^a-z]")


class VCResearchResult(BaseModel):
    investor_name: str
    website: str | None = None
    industry_focus: str = SECTOR_GENERALIST
    stage: str | None = None
    category: str = CATEGORY_INVESTOR
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
    confidence: float = Field(ge=0.0, le=1.0)
    source_urls: list[str] = Field(default_factory=list)
    notes: str | None = None

    @field_validator("industry_focus", mode="before")
    @classmethod
    def _normalize_industry_focus(cls, value: str | None) -> str:
        if not value:
            return SECTOR_GENERALIST
        return _SECTOR_ALIASES.get(str(value).strip().casefold(), SECTOR_GENERALIST)

    @field_validator("category", mode="before")
    @classmethod
    def _normalize_category(cls, value: str | None) -> str:
        if not value:
            return CATEGORY_INVESTOR
        return _CATEGORY_ALIASES.get(str(value).strip().casefold(), CATEGORY_INVESTOR)

    @field_validator("stage", mode="before")
    @classmethod
    def _normalize_stage(cls, value: str | None) -> str | None:
        """Utilyze only cares whether a firm invests at Pre Seed — every
        other stage (Seed, Series A, ...) is recorded as blank rather than
        as free text, so the column stays a simple yes/no signal."""
        if not value:
            return None
        alpha_only = _ALPHA_ONLY.sub("", str(value).casefold())
        return "Pre Seed" if alpha_only == "preseed" else None


class VCRecheckResult(BaseModel):
    """Cheap-path recheck contract for a row that's merely STALE, not new.

    Deliberately narrow: a recheck only re-verifies whether the deadline
    and application link are still accurate, not the firm's entire contact
    picture (email, phone, stage, etc. don't need re-researching every 90
    days — they rarely change once found).
    """

    investor_name: str
    deadline: str | None = None
    application_link: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    source_urls: list[str] = Field(default_factory=list)
    notes: str | None = None
