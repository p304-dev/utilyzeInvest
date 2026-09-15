"""Deadline normalization for the Wix publish payload.

A sheet Deadline cell is one free-text value: a real date, the word
"Rolling" (or a variant), or blank/the sentinel. Passing that straight
through breaks on the Wix site in two ways: "Rolling" can't sort next to
real dates, and a passed deadline never stops looking "open" on its own.
This expands the one cell into several purpose-built fields instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from framework.worker_base import is_effectively_blank

_ROLLING_WORDS = frozenset(
    {"rolling", "ongoing", "open", "continuous", "always open", "n/a", "none", "tbd"}
)

_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y")

# Sort anchors so "Rolling" and "Passed" land at the right ends of a
# date-sorted list instead of wherever their un-parseable text would fall.
_SORT_FAR_FUTURE = date(2099, 12, 31)
_SORT_FAR_PAST = date(1900, 1, 1)

_CLOSING_SOON_WINDOW_DAYS = 14


@dataclass(frozen=True)
class DeadlineFields:
    label: str
    date_iso: str | None
    sort_iso: str
    is_rolling: bool
    days_left: int | None
    status: str


def _parse_date(text: str) -> date | None:
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def compute_deadline_fields(
    raw_deadline: str | None,
    raw_status: str | None,
    *,
    today: date | None = None,
) -> DeadlineFields:
    """raw_deadline/raw_status come straight from the sheet's `Deadline` and
    `Deadline Status` columns. Either can independently signal "rolling" —
    a firm's status can already say ROLLING even when the raw text is
    something looser like "N/A"."""
    today = today or date.today()
    text = (raw_deadline or "").strip()
    status_hint = (raw_status or "").strip().upper()

    is_rolling = text.casefold() in _ROLLING_WORDS or status_hint == "ROLLING"
    parsed = None if (is_rolling or is_effectively_blank(text)) else _parse_date(text)
    days_left = (parsed - today).days if parsed else None

    if is_rolling:
        status = "ROLLING"
    elif days_left is not None and days_left < 0:
        status = "PASSED"
    elif days_left is not None and days_left <= _CLOSING_SOON_WINDOW_DAYS:
        status = "CLOSING SOON"
    elif days_left is not None:
        status = "OPEN"
    else:
        status = "UNKNOWN"

    if is_rolling or status == "UNKNOWN":
        sort_date = _SORT_FAR_FUTURE
    elif status == "PASSED":
        sort_date = _SORT_FAR_PAST
    else:
        sort_date = parsed

    if parsed:
        label = f"{parsed.strftime('%B')} {parsed.day}, {parsed.year}"
    elif is_rolling:
        label = "Rolling"
    else:
        label = text or "—"

    return DeadlineFields(
        label=label,
        date_iso=parsed.isoformat() if parsed else None,
        sort_iso=sort_date.isoformat(),
        is_rolling=is_rolling,
        days_left=days_left,
        status=status,
    )
