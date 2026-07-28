"""gspread-backed Sheets client. Resolves every column by header name at
runtime — never by a fixed index — because the real Investors tab has
trailing blank columns and headers that may shift.
"""

from __future__ import annotations

from typing import Any, Protocol

import gspread
from google.oauth2.service_account import Credentials

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

ROW_NUMBER_KEY = "_row_number"


class WorksheetLike(Protocol):
    """The minimal gspread Worksheet surface SheetsClient depends on. Lets
    tests substitute a lightweight fake instead of a real gspread object,
    while still exercising SheetsClient's real header-resolution logic."""

    def row_values(self, row: int) -> list[str]: ...
    def get_all_values(self) -> list[list[str]]: ...
    def update_cell(self, row: int, col: int, value: Any) -> None: ...
    def update_cells(self, cell_list: list[Any]) -> None: ...
    def cell(self, row: int, col: int) -> Any: ...


class MissingColumnError(Exception):
    """Raised when a worker's column_map targets a header that doesn't
    exist in the sheet. Almost always a config typo, not a legitimately
    new column — so we fail loudly instead of silently creating it."""


class SheetsClient:
    HEADER_ROW = 1

    def __init__(
        self,
        worksheet: WorksheetLike,
        *,
        header_row: int = HEADER_ROW,
    ) -> None:
        self._ws = worksheet
        self._header_row = header_row
        self._headers: list[str] = []
        self._header_to_col: dict[str, int] = {}
        self._refresh_headers()

    @classmethod
    def from_service_account(
        cls,
        *,
        credentials_path: str,
        spreadsheet_id: str,
        sheet_tab: str,
    ) -> "SheetsClient":
        creds = Credentials.from_service_account_file(credentials_path, scopes=_SCOPES)
        gc = gspread.authorize(creds)
        worksheet = gc.open_by_key(spreadsheet_id).worksheet(sheet_tab)
        return cls(worksheet)

    # -- header resolution -------------------------------------------------

    def _refresh_headers(self) -> None:
        raw_headers = self._ws.row_values(self._header_row)
        self._headers = raw_headers
        self._header_to_col = {
            header: idx + 1
            for idx, header in enumerate(raw_headers)
            if header  # skip blank header cells
        }

    def has_column(self, header: str) -> bool:
        return header in self._header_to_col

    def require_columns(self, headers: list[str]) -> None:
        """Raise MissingColumnError if any of these headers isn't present.
        Used for business columns a worker's column_map targets — those
        must already exist in the real sheet; the worker never invents
        new business columns for itself."""
        missing = [h for h in headers if h not in self._header_to_col]
        if missing:
            raise MissingColumnError(f"Missing expected column(s): {missing}")

    def ensure_columns(self, required_headers: list[str]) -> None:
        """Create any header in required_headers that's missing, writing
        only the header cell. Reuses the first blank header slot found
        after the existing headers before appending past the end (the real
        sheet has trailing blank columns). Idempotent."""
        missing = [h for h in required_headers if h not in self._header_to_col]
        if not missing:
            return

        # Find blank slots within the current header row first.
        blank_slots = [idx + 1 for idx, h in enumerate(self._headers) if not h]
        next_col = len(self._headers) + 1

        for header in missing:
            if blank_slots:
                col = blank_slots.pop(0)
            else:
                col = next_col
                next_col += 1
            self._ws.update_cell(self._header_row, col, header)

        self._refresh_headers()

    # -- reads ---------------------------------------------------------

    def read_all_rows(self) -> list[dict[str, str]]:
        all_values = self._ws.get_all_values()
        if len(all_values) <= self._header_row:
            return []

        data_rows = all_values[self._header_row :]
        rows: list[dict[str, str]] = []
        for offset, values in enumerate(data_rows):
            row_number = self._header_row + 1 + offset
            row: dict[str, str] = {ROW_NUMBER_KEY: row_number}
            for header, col in self._header_to_col.items():
                idx = col - 1
                row[header] = values[idx] if idx < len(values) else ""
            rows.append(row)
        return rows

    # -- writes ----------------------------------------------------------

    def write_cells(self, row_number: int, updates: dict[str, str]) -> None:
        """updates: header -> value. Every header is resolved via the
        current header map (raises KeyError on an unknown header rather
        than guessing a position). Batches all cells for this row into a
        single gspread update_cells call."""
        if not updates:
            return

        cells = []
        for header, value in updates.items():
            if header not in self._header_to_col:
                raise KeyError(f"Unknown column header: {header!r}")
            col = self._header_to_col[header]
            cell = self._ws.cell(row_number, col)
            cell.value = value
            cells.append(cell)

        self._ws.update_cells(cells)
