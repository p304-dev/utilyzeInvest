"""gspread-backed Sheets client. Resolves every column by header name at
runtime — never by a fixed index — because the real Investors tab has
trailing blank columns and headers that may shift.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

import gspread
from gspread import Cell
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
        path = Path(credentials_path)
        if not path.exists():
            raise RuntimeError(
                f"Service account credentials file not found: {credentials_path!r}. "
                "Check GOOGLE_APPLICATION_CREDENTIALS points at a real file."
            )
        if path.stat().st_size == 0:
            raise RuntimeError(
                f"Service account credentials file is empty: {credentials_path!r}. "
                "In CI this usually means the GOOGLE_SERVICE_ACCOUNT_JSON_B64 secret "
                "is unset or empty — check it's configured under repo Settings > "
                "Secrets and variables > Actions."
            )
        if not spreadsheet_id:
            raise RuntimeError(
                "SHEET_ID is not set. Check the SHEET_ID secret/env var points at "
                "the target spreadsheet's ID."
            )

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

    def column_index(self, header: str) -> int:
        """1-based column index for a header. Raises KeyError if absent."""
        return self._header_to_col[header]

    def require_columns(self, headers: list[str]) -> None:
        """Raise MissingColumnError if any of these headers isn't present.
        Used for business columns a worker's column_map targets — those
        must already exist in the real sheet; the worker never invents
        new business columns for itself."""
        missing = [h for h in headers if h not in self._header_to_col]
        if missing:
            raise MissingColumnError(f"Missing expected column(s): {missing}")

    def ensure_columns(self, required_headers: list[str]) -> None:
        """Create any missing header, writing only the header cell.

        New columns are always appended to the right of every column that
        holds data anywhere in the sheet — never dropped into a blank slot
        in the middle. The Investors tab's queue formula scans a fixed
        range (D:O) and has a notes column with no header, so reusing an
        interior blank would silently change queue semantics or clobber
        human notes. Idempotent.
        """
        missing = [h for h in required_headers if h not in self._header_to_col]
        if not missing:
            return

        next_col = self._used_width() + 1
        for header in missing:
            self._ws.update_cell(self._header_row, next_col, header)
            next_col += 1

        self._refresh_headers()

    def _used_width(self) -> int:
        """Widest row in the sheet, in columns. A column can hold data
        under a blank header (the Investors tab keeps freeform notes that
        way), so the header row alone understates the used range."""
        widths = [len(self._headers)]
        widths.extend(len(row) for row in self._ws.get_all_values())
        return max(widths, default=0)

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
        single gspread update_cells call.

        Cell objects are built locally (row/col/value only) rather than
        fetched via worksheet.cell(), which issues a live read per cell.
        We're about to overwrite the value anyway, so reading it first is
        pure waste — and at N fields per row, it was enough read traffic
        to blow through Sheets API's per-minute read quota on a run of
        more than a few dozen rows.
        """
        if not updates:
            return

        cells = []
        for header, value in updates.items():
            if header not in self._header_to_col:
                raise KeyError(f"Unknown column header: {header!r}")
            col = self._header_to_col[header]
            cells.append(Cell(row_number, col, value))

        self._ws.update_cells(cells)
