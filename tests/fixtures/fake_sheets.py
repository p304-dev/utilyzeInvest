"""An in-memory double implementing SheetsClient's public interface
(read_all_rows / write_cells / ensure_columns / has_column), for tests
that exercise framework/runner.py without touching gspread at all.
"""

from __future__ import annotations

from sheets.client import ROW_NUMBER_KEY


class FakeSheetsClient:
    def __init__(self, rows: list[dict[str, str]], headers: list[str] | None = None) -> None:
        self.rows: list[dict[str, str]] = []
        for i, row in enumerate(rows):
            row_copy = dict(row)
            row_copy.setdefault(ROW_NUMBER_KEY, i + 2)  # row 1 is the header
            self.rows.append(row_copy)

        headers = list(headers) if headers is not None else []
        for row in self.rows:
            for key in row:
                if key != ROW_NUMBER_KEY and key not in headers:
                    headers.append(key)
        self.headers: list[str] = headers

        # Every write_cells call, captured for assertions: list of
        # (row_number, updates) tuples, in call order.
        self.write_calls: list[tuple[int, dict[str, str]]] = []

    def has_column(self, header: str) -> bool:
        return header in self.headers

    def column_index(self, header: str) -> int:
        return self.headers.index(header) + 1

    def require_columns(self, headers: list[str]) -> None:
        missing = [h for h in headers if h not in self.headers]
        if missing:
            from sheets.client import MissingColumnError

            raise MissingColumnError(f"Missing expected column(s): {missing}")

    def ensure_columns(self, required_headers: list[str]) -> None:
        for header in required_headers:
            if header not in self.headers:
                self.headers.append(header)
                for row in self.rows:
                    row.setdefault(header, "")

    def read_all_rows(self) -> list[dict[str, str]]:
        return [dict(row) for row in self.rows]

    def write_cells(self, row_number: int, updates: dict[str, str]) -> None:
        if not updates:
            return
        self.write_calls.append((row_number, dict(updates)))
        for row in self.rows:
            if row[ROW_NUMBER_KEY] == row_number:
                row.update(updates)
                return
        raise KeyError(f"No row with row_number={row_number}")
