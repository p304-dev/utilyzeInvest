from __future__ import annotations

import json

import pytest

from config.settings import Settings
from framework.runner import RunnerOptions, run
from framework.worker_base import CORE_COLUMNS, RUNNER_DIRECT_COLUMNS
from sheets.client import MissingColumnError, ROW_NUMBER_KEY, SheetsClient
from tests.fixtures.fake_llm import FakeLLMClient
from tests.fixtures.fake_sheets import FakeSheetsClient
from tests.fixtures.fake_worksheet import FakeWorksheet
from workers.vc_research.worker import VCResearchWorker

REAL_HEADERS = [
    "Status",
    "Contact Date",
    "Investor Name",
    "Website",
    "Industry Focus",
    "Stage",
    "Email",
    "Phone",
    "City",
    "State/Country",
    "Deadline",
    "Application Link",
    "First",
    "Last",
    "Found",
    "",  # trailing blank column, as in the real sheet
    "",  # trailing blank column, as in the real sheet
]


def _make_sheet(headers: list[str], data_rows: list[list[str]]) -> SheetsClient:
    ws = FakeWorksheet([headers, *data_rows])
    return SheetsClient(ws)


# -- structural invariant: column_map can never target human-owned columns --


def test_worker_column_map_never_targets_human_owned_columns():
    worker = VCResearchWorker()
    assert not (set(worker.column_map.values()) & set(worker.human_owned_columns))


def test_worker_column_map_never_targets_runner_direct_columns():
    worker = VCResearchWorker()
    reserved = set(RUNNER_DIRECT_COLUMNS) | {worker.route_column}
    assert not (set(worker.column_map.values()) & reserved)


# -- header resolution: shuffled order + trailing blanks -----------------


def test_header_resolution_works_regardless_of_column_order():
    shuffled = ["Investor Name", "", "Status", "Website", "", "Contact Date", "Found"]
    sheet = _make_sheet(shuffled, [["Acme", "", "", "acme.vc", "", "", ""]])

    rows = sheet.read_all_rows()
    assert rows[0]["Investor Name"] == "Acme"
    assert rows[0]["Website"] == "acme.vc"

    sheet.write_cells(rows[0][ROW_NUMBER_KEY], {"Website": "https://acme.vc"})
    rows = sheet.read_all_rows()
    assert rows[0]["Website"] == "https://acme.vc"
    # Status column (index 2, 0-based) must be untouched by an update
    # targeted at Website (index 3).
    assert rows[0]["Status"] == ""


def test_ensure_columns_fills_trailing_blank_slots_before_appending():
    headers = ["Investor Name", "Website", "", ""]
    sheet = _make_sheet(headers, [["Acme", "acme.vc", "", ""]])

    sheet.ensure_columns(["Bot_Status", "Confidence"])

    rows = sheet.read_all_rows()
    assert sheet.has_column("Bot_Status")
    assert sheet.has_column("Confidence")
    # Filled into the two blank slots, not appended past them.
    assert len(rows[0]) - 1 <= 4  # -1 for ROW_NUMBER_KEY; no net-new columns


def test_ensure_columns_is_idempotent():
    sheet = _make_sheet(["Investor Name", "Bot_Status"], [["Acme", "Queued"]])
    sheet.ensure_columns(CORE_COLUMNS)
    headers_after_first = list(sheet._headers)
    sheet.ensure_columns(CORE_COLUMNS)
    assert sheet._headers == headers_after_first


def test_ensure_columns_creates_all_core_columns_on_a_bare_sheet():
    sheet = _make_sheet(["Investor Name", "Website"], [["Acme", "acme.vc"]])
    sheet.ensure_columns(CORE_COLUMNS)
    for header in CORE_COLUMNS:
        assert sheet.has_column(header)


def test_write_cells_targets_correct_column_by_name_not_index():
    sheet = _make_sheet(REAL_HEADERS, [
        ["", "", "Acme", "", "", "", "", "", "", "", "", "", "", "", "", "", ""],
    ])
    row_number = sheet.read_all_rows()[0][ROW_NUMBER_KEY]
    sheet.write_cells(row_number, {"Industry Focus": "Climate"})

    row = sheet.read_all_rows()[0]
    assert row["Industry Focus"] == "Climate"
    assert row["Status"] == ""
    assert row["Contact Date"] == ""
    assert row["Found"] == ""


def test_write_cells_rejects_unknown_header():
    sheet = _make_sheet(["Investor Name"], [["Acme"]])
    with pytest.raises(KeyError):
        sheet.write_cells(2, {"Not A Real Column": "x"})


def test_require_columns_raises_on_missing_business_column():
    sheet = _make_sheet(["Investor Name", "Website"], [["Acme", "acme.vc"]])
    with pytest.raises(MissingColumnError):
        sheet.require_columns(["Email"])


def test_require_columns_passes_when_present():
    sheet = _make_sheet(["Investor Name", "Email"], [["Acme", "a@acme.vc"]])
    sheet.require_columns(["Email"])  # should not raise


# -- runner write behavior: blank-only vs force-refresh vs bookkeeping ---

BASE_HEADERS = [
    "Status",
    "Contact Date",
    "Investor Name",
    "Website",
    "Industry Focus",
    "Stage",
    "Email",
    "Phone",
    "City",
    "State/Country",
    "Deadline",
    "Application Link",
    "First",
    "Last",
    "Found",
]


def _base_row(**overrides) -> dict[str, str]:
    row = {header: "" for header in BASE_HEADERS}
    row["Investor Name"] = "Acme Ventures"
    row.update(overrides)
    return row


def _run_once(rows, response_json, force_refresh=False):
    sheet = FakeSheetsClient(rows, headers=list(BASE_HEADERS))
    llm = FakeLLMClient([response_json])
    worker = VCResearchWorker()
    settings = Settings()
    options = RunnerOptions(batch_size=5, force_refresh=force_refresh)
    run(worker, sheet, llm, settings, options)
    return sheet


def test_runner_never_writes_human_owned_columns():
    rows = [_base_row()]
    response = json.dumps(
        {
            "investor_name": "Acme Ventures",
            "website": None,
            "industry_focus": None,
            "stage": None,
            "email": None,
            "phone": None,
            "city": None,
            "state_or_country": None,
            "deadline": None,
            "application_link": "https://apply.acme.vc",
            "contact_first_name": None,
            "contact_last_name": None,
            "has_contact_form": False,
            "draft_subject": "Intro",
            "draft_body": "Hello there",
            "confidence": 0.9,
            "source_urls": ["https://acme.vc"],
            "notes": None,
        }
    )
    sheet = _run_once(rows, response)
    assert sheet.write_calls, "expected at least one write"
    for _row_number, updates in sheet.write_calls:
        assert "Status" not in updates
        assert "Contact Date" not in updates
        assert "Found" not in updates


def test_runner_only_fills_blank_business_columns_by_default():
    rows = [_base_row(Website="https://existing.vc")]
    response = json.dumps(
        {
            "investor_name": "Acme Ventures",
            "website": "https://new.vc",
            "industry_focus": "Climate",
            "stage": None,
            "email": None,
            "phone": None,
            "city": None,
            "state_or_country": None,
            "deadline": None,
            "application_link": None,
            "contact_first_name": None,
            "contact_last_name": None,
            "has_contact_form": False,
            "draft_subject": "Intro",
            "draft_body": "Hello there",
            "confidence": 0.9,
            "source_urls": ["https://acme.vc"],
            "notes": None,
        }
    )
    sheet = _run_once(rows, response)
    final_row = sheet.rows[0]
    assert final_row["Website"] == "https://existing.vc"  # untouched: was non-blank
    assert final_row["Industry Focus"] == "Climate"  # filled: was blank


def test_runner_force_refresh_overwrites_non_blank_business_columns():
    rows = [_base_row(Website="https://existing.vc")]
    response = json.dumps(
        {
            "investor_name": "Acme Ventures",
            "website": "https://new.vc",
            "industry_focus": "Climate",
            "stage": None,
            "email": None,
            "phone": None,
            "city": None,
            "state_or_country": None,
            "deadline": None,
            "application_link": None,
            "contact_first_name": None,
            "contact_last_name": None,
            "has_contact_form": False,
            "draft_subject": "Intro",
            "draft_body": "Hello there",
            "confidence": 0.9,
            "source_urls": ["https://acme.vc"],
            "notes": None,
        }
    )
    sheet = _run_once(rows, response, force_refresh=True)
    final_row = sheet.rows[0]
    assert final_row["Website"] == "https://new.vc"  # overwritten under --force-refresh


def test_runner_always_writes_bookkeeping_columns():
    rows = [_base_row()]
    response = json.dumps(
        {
            "investor_name": "Acme Ventures",
            "website": None,
            "industry_focus": None,
            "stage": None,
            "email": None,
            "phone": None,
            "city": None,
            "state_or_country": None,
            "deadline": None,
            "application_link": None,
            "contact_first_name": None,
            "contact_last_name": None,
            "has_contact_form": False,
            "draft_subject": "Intro",
            "draft_body": "Hello there",
            "confidence": 0.9,
            "source_urls": ["https://acme.vc"],
            "notes": "some note",
        }
    )
    sheet = _run_once(rows, response)
    final_row = sheet.rows[0]
    assert final_row["Bot_Status"] == "Needs Review"
    assert final_row["Recommended_Channel"] == "LinkedIn"
    assert final_row["Confidence"] == "0.90"
    assert final_row["Source_URLs"] == "https://acme.vc"
    assert final_row["Draft_Status"] == "Drafted"
    assert final_row["Last_Checked"]


def test_runner_low_confidence_forces_needs_review_with_flag():
    rows = [_base_row()]
    response = json.dumps(
        {
            "investor_name": "Acme Ventures",
            "website": None,
            "industry_focus": None,
            "stage": None,
            "email": None,
            "phone": None,
            "city": None,
            "state_or_country": None,
            "deadline": None,
            "application_link": None,
            "contact_first_name": None,
            "contact_last_name": None,
            "has_contact_form": False,
            "draft_subject": "Intro",
            "draft_body": "Hello there",
            "confidence": 0.2,
            "source_urls": [],
            "notes": "ambiguous name",
        }
    )
    sheet = _run_once(rows, response)
    final_row = sheet.rows[0]
    assert final_row["Bot_Status"] == "Needs Review"
    assert "LOW CONFIDENCE" in final_row["Research_Notes"]


def test_runner_error_after_exhausted_retries_writes_no_business_columns():
    rows = [_base_row()]
    sheet = FakeSheetsClient(rows, headers=list(BASE_HEADERS))
    llm = FakeLLMClient(["not json", "still not json", "nope"])
    worker = VCResearchWorker()
    settings = Settings()
    options = RunnerOptions(batch_size=5)
    run(worker, sheet, llm, settings, options)

    assert len(llm.calls) == 3
    final_row = sheet.rows[0]
    assert final_row["Bot_Status"] == "Error"
    assert final_row["Website"] == ""
    assert final_row["Confidence"] == ""
