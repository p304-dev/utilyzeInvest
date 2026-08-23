"""The migration's load-bearing invariants: the queue is read from the
sheet's formula column, and no scanned cell is ever left blank.
"""

from __future__ import annotations

import json

import pytest

from config.settings import Settings
from framework.runner import RunnerOptions, run, select_queue
from tests.fixtures.fake_llm import FakeLLMClient
from tests.fixtures.fake_sheets import FakeSheetsClient
from tests.fixtures.investors_sheet import (
    INVESTORS_HEADERS,
    SCANNED_FIRST_COL,
    SCANNED_LAST_COL,
    investors_row,
)
from workers.vc_research.worker import VCResearchWorker

SCANNED_HEADERS = INVESTORS_HEADERS[SCANNED_FIRST_COL - 1 : SCANNED_LAST_COL]


def _response(**overrides) -> str:
    base = {
        "investor_name": "Acme Ventures",
        "website": "https://acme.vc",
        "industry_focus": "Climate",
        "stage": "Pre Seed",
        "email": None,
        "phone": None,
        "city": "Austin",
        "state_or_country": "Texas",
        "deadline": "Rolling",
        "application_link": None,
        "linkedin_url": "https://linkedin.com/company/acme",
        "twitter_url": None,
        "newsletter": None,
        "has_contact_form": False,
        "draft_subject": "Intro",
        "draft_body": "Hello there",
        "confidence": 0.9,
        "source_urls": ["https://acme.vc"],
        "notes": None,
    }
    base.update(overrides)
    return json.dumps(base)


def _settings() -> Settings:
    return Settings(_env_file=None, fetch_first=False)


def _run(rows, responses=None, **option_overrides):
    sheet = FakeSheetsClient(rows, headers=list(INVESTORS_HEADERS))
    llm = FakeLLMClient(responses or [_response()])
    worker = VCResearchWorker()
    options = RunnerOptions(batch_size=10, **option_overrides)
    run(worker, sheet, llm, _settings(), options)
    return sheet, llm


# -- queue selection comes from column Q ---------------------------------


@pytest.mark.parametrize("queue_value", ["PULL VC DATA", "STALE", "  stale  ", "pull vc data"])
def test_rows_the_formula_flags_are_selected(queue_value):
    rows = [investors_row(**{"Deadline Formula": queue_value})]
    queue = select_queue(rows, VCResearchWorker(), RunnerOptions(batch_size=10))
    assert len(queue) == 1


@pytest.mark.parametrize("queue_value", ["CURRENT", "", "  ", "anything else"])
def test_rows_the_formula_clears_are_never_selected(queue_value):
    rows = [investors_row(**{"Deadline Formula": queue_value})]
    queue = select_queue(rows, VCResearchWorker(), RunnerOptions(batch_size=10))
    assert queue == []


def test_current_row_is_not_processed_even_with_blank_scanned_cells():
    # The formula owns the decision; Python must not second-guess it by
    # re-scanning for blanks the way the old queue did.
    rows = [investors_row(**{"Deadline Formula": "CURRENT"})]
    sheet, llm = _run(rows)
    assert sheet.write_calls == []
    assert llm.calls == []


def test_error_rows_are_held_back_unless_requeued():
    rows = [investors_row(Bot_Status="Error")]
    worker = VCResearchWorker()
    assert select_queue(rows, worker, RunnerOptions(batch_size=10)) == []
    assert len(select_queue(rows, worker, RunnerOptions(batch_size=10, requeue=True))) == 1


def test_force_refresh_processes_a_current_row():
    rows = [investors_row(**{"Deadline Formula": "CURRENT"})]
    queue = select_queue(
        rows, VCResearchWorker(), RunnerOptions(batch_size=10, force_refresh=True)
    )
    assert len(queue) == 1


# -- the sentinel rule ---------------------------------------------------


def test_null_fields_become_sentinels_and_no_scanned_cell_is_left_blank():
    rows = [investors_row()]
    sheet, _ = _run(rows, [_response(phone=None, twitter_url=None, newsletter=None)])

    final = sheet.rows[0]
    assert final["Phone"] == "None"
    assert final["Twitter"] == "None"
    assert final["Newsletter Yes/No"] == "None"

    blank = [h for h in SCANNED_HEADERS if not (final.get(h) or "").strip()]
    assert blank == [], f"scanned cells left blank (row would re-queue forever): {blank}"


def test_empty_string_is_treated_as_null_for_the_sentinel():
    rows = [investors_row()]
    sheet, _ = _run(rows, [_response(phone="   ", newsletter="")])
    assert sheet.rows[0]["Phone"] == "None"
    assert sheet.rows[0]["Newsletter Yes/No"] == "None"


def test_sentinel_counts_as_filled_and_is_not_rewritten():
    rows = [investors_row(Phone="None")]
    sheet, _ = _run(rows, [_response(phone="+1-555-0100")])
    # Already researched-and-absent; a later run must not churn the cell.
    assert sheet.rows[0]["Phone"] == "None"


def test_force_refresh_replaces_a_sentinel_with_a_real_value():
    rows = [investors_row(Phone="None")]
    sheet, _ = _run(rows, [_response(phone="+1-555-0100")], force_refresh=True)
    assert sheet.rows[0]["Phone"] == "+1-555-0100"


def test_real_values_are_not_overwritten_without_force_refresh():
    rows = [investors_row(Website="https://existing.vc")]
    sheet, _ = _run(rows, [_response(website="https://new.vc")])
    assert sheet.rows[0]["Website"] == "https://existing.vc"


# -- what the runner writes, and what it must never touch ----------------


def test_timestamp_lands_in_the_existing_last_checked_column():
    rows = [investors_row()]
    sheet, _ = _run(rows)
    assert sheet.rows[0]["Last Checked"]
    # A duplicate underscore variant would leave the formula's R cell blank.
    assert "Last_Checked" not in sheet.headers


def test_human_and_formula_columns_are_never_written():
    rows = [investors_row()]
    sheet, _ = _run(rows)
    for _row_number, updates in sheet.write_calls:
        for forbidden in ("Status", "Contact Date", "Category", "Deadline Formula",
                          "Deadline Status"):
            assert forbidden not in updates


def test_researching_status_is_no_longer_written():
    rows = [investors_row()]
    sheet, _ = _run(rows)
    statuses = [u.get("Bot_Status") for _r, u in sheet.write_calls if "Bot_Status" in u]
    assert "Researching" not in statuses
    assert statuses == ["Needs Review"]
    # One write per row, not two.
    assert len(sheet.write_calls) == 1


def test_error_row_writes_no_business_columns():
    rows = [investors_row()]
    sheet, llm = _run(rows, ["not json", "still not json", "nope"])
    assert len(llm.calls) == 3
    final = sheet.rows[0]
    assert final["Bot_Status"] == "Error"
    assert final["Website"] == ""
    assert final["Last Checked"]


# -- the D:O column-range invariant --------------------------------------


def test_no_bot_owned_column_lands_inside_the_scanned_range():
    rows = [investors_row()]
    sheet, _ = _run(rows)
    worker = VCResearchWorker()
    for header in worker.owned_columns:
        index = sheet.column_index(header)
        assert not (SCANNED_FIRST_COL <= index <= SCANNED_LAST_COL), (
            f"{header!r} landed in the COUNTBLANK range at column {index}"
        )


def test_runner_rejects_a_bot_column_inside_the_scanned_range():
    # Simulate a sheet where someone inserted a bot column into D:O.
    headers = list(INVESTORS_HEADERS)
    headers.insert(SCANNED_FIRST_COL - 1, "Bot_Status")
    sheet = FakeSheetsClient([investors_row()], headers=headers)
    with pytest.raises(AssertionError, match="formula-scanned range"):
        run(
            VCResearchWorker(),
            sheet,
            FakeLLMClient([_response()]),
            _settings(),
            RunnerOptions(batch_size=10),
        )


def test_last_checked_must_already_exist():
    headers = [h for h in INVESTORS_HEADERS if h != "Last Checked"]
    row = investors_row()
    del row["Last Checked"]
    sheet = FakeSheetsClient([row], headers=headers)
    from sheets.client import MissingColumnError

    with pytest.raises(MissingColumnError, match="Last Checked"):
        run(
            VCResearchWorker(),
            sheet,
            FakeLLMClient([_response()]),
            _settings(),
            RunnerOptions(batch_size=10),
        )
