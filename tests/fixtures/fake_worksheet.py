"""A minimal in-memory double for a gspread Worksheet, matching just the
surface SheetsClient depends on (sheets/client.py: WorksheetLike). Used to
exercise SheetsClient's real header-resolution logic without any network
calls or the real gspread object graph.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FakeCell:
    row: int
    col: int
    value: str


class FakeWorksheet:
    def __init__(self, rows: list[list[str]]) -> None:
        self._rows: list[list[str]] = [list(r) for r in rows]

    def _ensure_size(self, row: int, col: int) -> None:
        while len(self._rows) < row:
            self._rows.append([])
        while len(self._rows[row - 1]) < col:
            self._rows[row - 1].append("")

    def row_values(self, row: int) -> list[str]:
        idx = row - 1
        if idx < len(self._rows):
            return list(self._rows[idx])
        return []

    def get_all_values(self) -> list[list[str]]:
        return [list(r) for r in self._rows]

    def update_cell(self, row: int, col: int, value: str) -> None:
        self._ensure_size(row, col)
        self._rows[row - 1][col - 1] = value

    def cell(self, row: int, col: int) -> FakeCell:
        self._ensure_size(row, col)
        return FakeCell(row=row, col=col, value=self._rows[row - 1][col - 1])

    def update_cells(self, cell_list: list[FakeCell]) -> None:
        for c in cell_list:
            self._ensure_size(c.row, c.col)
            self._rows[c.row - 1][c.col - 1] = c.value
