"""Industry Focus, Category, and Stage are all constrained vocabularies,
not free text — the model is instructed to stick to the list, but these
tests check the schema itself enforces it, since a prompt instruction
alone is not a guarantee.

Industry Focus (which sector) and Category (what kind of opportunity —
Investor/Accelerator/Grant/Pitch/Research, now that those all live on the
same tab) are two different questions about a row; each has its own
taxonomy and its own fallback.
"""

from __future__ import annotations

from workers.vc_research.schema import (
    CATEGORIES,
    CATEGORY_ACCELERATOR,
    CATEGORY_GRANT,
    CATEGORY_INVESTOR,
    CATEGORY_PITCH,
    CATEGORY_RESEARCH,
    SECTOR_BIOTECH,
    SECTOR_CLIMATE,
    SECTOR_GENERALIST,
    SECTOR_UTILITIES,
    SECTOR_WATER,
    SECTORS,
    VCResearchResult,
)


def _result(**overrides) -> VCResearchResult:
    base = dict(investor_name="Acme Ventures", has_contact_form=False, confidence=0.9)
    base.update(overrides)
    return VCResearchResult(**base)


# -- Industry Focus: which sector -----------------------------------------


def test_sectors_are_exactly_the_five_utilyze_buckets():
    assert set(SECTORS) == {"Generalist", "Climate", "Biotech", "Utilities", "Water"}


def test_industry_focus_defaults_to_generalist_when_omitted():
    assert _result().industry_focus == SECTOR_GENERALIST


def test_industry_focus_defaults_to_generalist_when_null():
    assert _result(industry_focus=None).industry_focus == SECTOR_GENERALIST


def test_each_canonical_sector_round_trips():
    for value in SECTORS:
        assert _result(industry_focus=value).industry_focus == value


def test_an_off_list_sector_falls_back_to_generalist():
    # "Fintech" is a real classification, just not one of the five Utilyze
    # wants — it must not slip through as a sixth, invented sector.
    assert _result(industry_focus="Fintech").industry_focus == SECTOR_GENERALIST


def test_common_sector_phrasing_variants_are_normalized():
    assert _result(industry_focus="bio-tech").industry_focus == SECTOR_BIOTECH
    assert _result(industry_focus="Bio Tech").industry_focus == SECTOR_BIOTECH
    assert _result(industry_focus="cleantech").industry_focus == SECTOR_CLIMATE
    assert _result(industry_focus="utility").industry_focus == SECTOR_UTILITIES
    assert _result(industry_focus="water tech").industry_focus == SECTOR_WATER


def test_sector_normalization_is_case_insensitive():
    assert _result(industry_focus="CLIMATE").industry_focus == SECTOR_CLIMATE
    assert _result(industry_focus="  water  ").industry_focus == SECTOR_WATER


# -- Category: what kind of opportunity -----------------------------------


def test_categories_are_exactly_the_five_row_types():
    assert set(CATEGORIES) == {"Investor", "Accelerator", "Grant", "Pitch", "Research"}


def test_category_defaults_to_investor_when_omitted():
    assert _result().category == CATEGORY_INVESTOR


def test_category_defaults_to_investor_when_null():
    assert _result(category=None).category == CATEGORY_INVESTOR


def test_each_canonical_category_round_trips():
    for value in CATEGORIES:
        assert _result(category=value).category == value


def test_an_off_list_category_falls_back_to_investor():
    assert _result(category="Fintech Fund").category == CATEGORY_INVESTOR


def test_common_category_phrasing_variants_are_normalized():
    assert _result(category="incubator").category == CATEGORY_ACCELERATOR
    assert _result(category="grant program").category == CATEGORY_GRANT
    assert _result(category="pitch competition").category == CATEGORY_PITCH
    assert _result(category="research topic").category == CATEGORY_RESEARCH
    assert _result(category="vc").category == CATEGORY_INVESTOR


def test_category_normalization_is_case_insensitive():
    assert _result(category="GRANT").category == CATEGORY_GRANT
    assert _result(category="  pitch  ").category == CATEGORY_PITCH


# -- Stage: only ever Pre Seed or blank ------------------------------------


def test_stage_is_blank_when_omitted():
    assert _result().stage is None


def test_stage_recognizes_pre_seed_regardless_of_formatting():
    for value in ("Pre Seed", "pre-seed", "PreSeed", "  pre   seed  "):
        assert _result(stage=value).stage == "Pre Seed"


def test_any_other_stage_is_normalized_to_blank():
    for value in ("Seed", "Series A", "Series B", "Growth", "Pre-Series A"):
        assert _result(stage=value).stage is None
