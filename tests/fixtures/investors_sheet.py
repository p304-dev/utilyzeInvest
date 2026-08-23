"""The real Investors tab layout, A through S.

Column order is load-bearing: the sheet's queue formula scans D:O with
COUNTBLANK, so a test that gets these positions wrong would silently stop
testing the thing it claims to test.
"""

from __future__ import annotations

INVESTORS_HEADERS: list[str] = [
    "Status",  # A
    "Contact Date",  # B
    "Name",  # C
    "Website",  # D  <- start of the COUNTBLANK range
    "Industry Focus",  # E
    "Stage",  # F
    "Email",  # G
    "Phone",  # H
    "City",  # I
    "State/Country",  # J
    "Deadline",  # K
    "Application Link",  # L
    "LinkedIn",  # M
    "Twitter",  # N
    "Newsletter Yes/No",  # O  <- end of the COUNTBLANK range
    "Category",  # P
    "Deadline Formula",  # Q
    "Last Checked",  # R
    "Deadline Status",  # S
]

SCANNED_FIRST_COL = 4  # D
SCANNED_LAST_COL = 15  # O


def investors_row(**overrides: str) -> dict[str, str]:
    """A blank Investors row, queued for work by default."""
    row = {header: "" for header in INVESTORS_HEADERS}
    row["Name"] = "Acme Ventures"
    row["Category"] = "Investor"
    row["Deadline Formula"] = "PULL VC DATA"
    row.update(overrides)
    return row
