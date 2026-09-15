"""The whole point of this feature is to stop the LLM from re-researching
fields that are already sitting in the row — whether a human filled them
in, or a prior sentinel already confirmed they don't exist. These tests
check the prompt actually reflects that, since a prompt-text bug here is
easy to miss (nothing fails loudly — it just quietly costs money).
"""

from __future__ import annotations

from config.settings import Settings
from framework.worker_base import SENTINEL_VALUE
from tests.fixtures.investors_sheet import investors_row
from workers.vc_research.worker import VCResearchWorker


def _settings() -> Settings:
    return Settings(_env_file=None)


def test_a_field_a_human_already_filled_is_surfaced_as_known():
    worker = VCResearchWorker()
    row = investors_row(City="Austin", **{"State/Country": "Texas"})
    prompt = worker.build_prompt(row, _settings())
    assert "City: Austin" in prompt
    assert "State/Country: Texas" in prompt


def test_known_fields_are_told_not_to_be_researched_or_sourced():
    worker = VCResearchWorker()
    row = investors_row(City="Austin")
    prompt = worker.build_prompt(row, _settings())
    assert "do not" in prompt.lower() and "re-verify" in prompt.lower()


def test_a_prior_sentinel_is_surfaced_as_confirmed_absent_not_as_a_value():
    worker = VCResearchWorker()
    row = investors_row(Phone=SENTINEL_VALUE)
    prompt = worker.build_prompt(row, _settings())
    assert "Phone" in prompt
    assert "confirmed" in prompt.lower() and "not publicly available" in prompt.lower()
    # It must never be echoed as if the sentinel were a literal known value.
    assert f"Phone: {SENTINEL_VALUE}" not in prompt


def test_a_row_with_nothing_prefilled_says_so_explicitly():
    worker = VCResearchWorker()
    row = investors_row()  # every field blank except Name/queue column
    prompt = worker.build_prompt(row, _settings())
    assert "Nothing pre-filled" in prompt


def test_website_is_never_listed_as_a_known_field_to_skip():
    # Website is surfaced separately, up top, as an identity hint — it
    # would be confusing (and redundant) to also list it in the skip-list.
    worker = VCResearchWorker()
    row = investors_row(Website="https://acme.vc")
    prompt = worker.build_prompt(row, _settings())
    known_section = prompt.split("## Already known for this row")[1].split("##")[0]
    assert "Website" not in known_section


def test_always_overwrite_fields_are_never_listed_as_known():
    # Confidence/Source_URLs/Research_Notes are the worker's own
    # assessment, recomputed every pass — never a "don't research this"
    # signal even if a prior pass already populated them.
    worker = VCResearchWorker()
    row = investors_row(Confidence="0.90", Source_URLs="https://acme.vc")
    prompt = worker.build_prompt(row, _settings())
    known_section = prompt.split("## Already known for this row")[1].split("##")[0]
    assert "Confidence" not in known_section
    assert "Source_URLs" not in known_section


def test_mixed_row_surfaces_both_known_values_and_confirmed_absent_fields():
    worker = VCResearchWorker()
    row = investors_row(City="Austin", Phone=SENTINEL_VALUE, Twitter=SENTINEL_VALUE)
    prompt = worker.build_prompt(row, _settings())
    known_section = prompt.split("## Already known for this row")[1].split("##")[0]
    assert "City: Austin" in known_section
    assert "Phone" in known_section
    assert "Twitter" in known_section
