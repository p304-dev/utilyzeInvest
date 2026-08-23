"""Header resolution and column-creation behavior of SheetsClient, plus
the structural guards on a worker's column_map.
"""

from __future__ import annotations

import pytest

from framework.worker_base import CORE_COLUMNS
from sheets.client import MissingColumnError, ROW_NUMBER_KEY, SheetsClient
from tests.fixtures.fake_worksheet import FakeWorksheet
from tests.fixtures.investors_sheet import (
    INVESTORS_HEADERS,
    SCANNED_FIRST_COL,
    SCANNED_LAST_COL,
)
from workers.vc_research.worker import VCResearchWorker


def _make_sheet(headers: list[str], data_rows: list[list[str]]) -> SheetsClient:
    return SheetsClient(FakeWorksheet([headers, *data_rows]))


# -- structural invariants on the worker contract ------------------------


def test_column_map_never_targets_human_owned_or_formula_columns():
    worker = VCResearchWorker()
    never_write = set(worker.human_owned_columns) | set(worker.formula_columns)
    assert not (set(worker.column_map.values()) & never_write)


def test_column_map_never_targets_runner_managed_columns():
    worker = VCResearchWorker()
    reserved = {"Bot_Status", worker.timestamp_column, worker.route_column}
    assert not (set(worker.column_map.values()) & reserved)


def test_every_sentinel_column_is_a_column_map_target():
    worker = VCResearchWorker()
    assert set(worker.sentinel_columns) <= set(worker.column_map.values())


def test_sentinel_columns_are_exactly_the_scanned_range():
    worker = VCResearchWorker()
    expected = set(INVESTORS_HEADERS[SCANNED_FIRST_COL - 1 : SCANNED_LAST_COL])
    assert set(worker.sentinel_columns) == expected


def test_load_bearing_columns_are_required_not_auto_created():
    worker = VCResearchWorker()
    for header in ("Last Checked", "Deadline Formula", "Deadline Status"):
        assert header in worker.required_columns
        assert header not in worker.owned_columns


# -- header resolution ---------------------------------------------------


def test_header_resolution_works_regardless_of_column_order():
    shuffled = ["Name", "", "Status", "Website", "", "Contact Date"]
    sheet = _make_sheet(shuffled, [["Acme", "", "", "acme.vc", "", ""]])

    rows = sheet.read_all_rows()
    assert rows[0]["Name"] == "Acme"
    assert rows[0]["Website"] == "acme.vc"

    sheet.write_cells(rows[0][ROW_NUMBER_KEY], {"Website": "https://acme.vc"})
    rows = sheet.read_all_rows()
    assert rows[0]["Website"] == "https://acme.vc"
    assert rows[0]["Status"] == ""


def test_write_cells_targets_correct_column_by_name_not_index():
    sheet = _make_sheet(INVESTORS_HEADERS, [[""] * len(INVESTORS_HEADERS)])
    row_number = sheet.read_all_rows()[0][ROW_NUMBER_KEY]
    sheet.write_cells(row_number, {"Industry Focus": "Climate"})

    row = sheet.read_all_rows()[0]
    assert row["Industry Focus"] == "Climate"
    assert row["Status"] == ""
    assert row["Category"] == ""


def test_write_cells_rejects_unknown_header():
    sheet = _make_sheet(["Name"], [["Acme"]])
    with pytest.raises(KeyError):
        sheet.write_cells(2, {"Not A Real Column": "x"})


def test_require_columns_raises_on_missing_column():
    sheet = _make_sheet(["Name", "Website"], [["Acme", "acme.vc"]])
    with pytest.raises(MissingColumnError):
        sheet.require_columns(["Email"])


def test_require_columns_passes_when_present():
    sheet = _make_sheet(["Name", "Email"], [["Acme", "a@acme.vc"]])
    sheet.require_columns(["Email"])  # should not raise


# -- column creation never disturbs the scanned range --------------------


def test_new_columns_append_past_every_used_column():
    # A column can hold data under a blank header — the Investors tab keeps
    # freeform notes that way — so appending must clear the widest row, not
    # just the header row.
    headers = ["Name", "Website"]
    rows = [["Acme", "acme.vc", "", "a human note in an unheadered column"]]
    sheet = _make_sheet(headers, rows)

    sheet.ensure_columns(["Bot_Status"])

    assert sheet.column_index("Bot_Status") == 5
    assert sheet.read_all_rows()[0]["Bot_Status"] == ""


def test_ensure_columns_never_fills_an_interior_blank():
    headers = ["Name", "", "Website"]
    sheet = _make_sheet(headers, [["Acme", "", "acme.vc"]])
    sheet.ensure_columns(["Bot_Status"])
    # Column 2 is blank but reserved; the new header goes after column 3.
    assert sheet.column_index("Bot_Status") == 4


def test_ensure_columns_is_idempotent():
    sheet = _make_sheet(["Name", "Bot_Status"], [["Acme", ""]])
    sheet.ensure_columns(CORE_COLUMNS)
    headers_after_first = list(sheet._headers)
    sheet.ensure_columns(CORE_COLUMNS)
    assert sheet._headers == headers_after_first


def test_owned_columns_land_outside_the_scanned_range_on_the_real_layout():
    sheet = _make_sheet(INVESTORS_HEADERS, [[""] * len(INVESTORS_HEADERS)])
    worker = VCResearchWorker()
    sheet.ensure_columns(worker.owned_columns)

    for header in worker.owned_columns:
        index = sheet.column_index(header)
        assert index > SCANNED_LAST_COL
        assert not (SCANNED_FIRST_COL <= index <= SCANNED_LAST_COL)
