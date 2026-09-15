"""The Worker contract. Every worker (vc_research, grants, and future ones)
implements this; framework/runner.py only ever depends on this interface,
never on a specific worker's schema, columns, or sheet layout.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel

# Bot-owned bookkeeping columns every worker shares. Auto-created to the
# right of all existing data if missing (see sheets/client.py:
# ensure_columns). Confidence/Source_URLs/Research_Notes are populated via
# a worker's column_map / compute_business_updates(); Bot_Status is written
# directly by the runner.
CORE_COLUMNS: list[str] = [
    "Bot_Status",
    "Confidence",
    "Source_URLs",
    "Research_Notes",
]

# Bot_Status is an *outcome log*, not queue state. The queue lives in the
# spreadsheet (see Worker.queue_column) and clears itself once the worker
# fills the row. There is deliberately no "Researching" value: it cost an
# extra write per row and nothing ever read it.
BOT_STATUS_NEEDS_REVIEW = "Needs Review"
BOT_STATUS_ERROR = "Error"

# Written into any worker-owned cell the sheet's queue formula scans
# (Worker.sentinel_columns) when the LLM found nothing. COUNTBLANK cannot
# tell "never researched" from "researched, doesn't exist" — without this,
# a firm with no public phone re-queues on every run, forever, at cost.
# An em-dash rather than a real blank: still non-blank to COUNTBLANK, but
# reads as an ordinary "nothing here" marker instead of a stray "None".
SENTINEL_VALUE = "—"


def is_effectively_blank(value: str | None) -> bool:
    """True for a cell that holds no researched information — empty, or
    the sentinel we wrote to mark 'researched, doesn't exist'. Use this
    whenever reading a cell as *input* (e.g. the Website hint), so a
    sentinel is never fed back to the LLM as if it were real data."""
    if value is None:
        return True
    stripped = value.strip()
    return stripped == "" or stripped == SENTINEL_VALUE


def format_value(value: Any) -> str:
    """Render a validated schema field as a sheet-cell string."""
    if isinstance(value, list):
        return "\n".join(str(v) for v in value)
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


class Worker(ABC):
    """Contract a concrete worker (e.g. vc_research, grants) must implement."""

    name: str
    sheet_tab: str
    input_columns: list[str]
    output_schema: type[BaseModel]

    # pydantic field name -> sheet header, for fields that follow the
    # standard "write once to a blank cell" rule via the default
    # compute_business_updates() below. A worker with composite/joined
    # columns (see grants) can override compute_business_updates() entirely
    # and use column_map purely as a declaration of its simple fields.
    column_map: dict[str, str]

    # Subset of column_map keys that represent the worker's own assessment
    # (confidence, source_urls, notes, draft copy, ...) rather than
    # ground-truth business data a human might also fill in by hand. These
    # are overwritten every time a row is processed, regardless of
    # --force-refresh. Everything else in column_map is written only to a
    # blank cell unless --force-refresh is passed.
    always_overwrite_fields: frozenset[str] = frozenset()

    # Columns a human owns on this worker's sheet/tab — never written,
    # under any flag.
    human_owned_columns: list[str]

    # Columns holding spreadsheet formulas — never written, under any flag.
    # Writing one would replace the formula with a literal and break the
    # queue permanently.
    formula_columns: list[str] = []

    # Worker-owned columns the sheet's queue formula scans for blanks. Every
    # one is guaranteed non-blank after a row is processed — real value or
    # SENTINEL_VALUE. Empty means the worker's tab has no such formula.
    sentinel_columns: frozenset[str] = frozenset()

    # The header holding the queue decision, and the values meaning "process
    # this row" (compared trimmed + case-insensitively). None means this
    # tab has no queue formula, and the runner falls back to scanning for
    # blank business columns.
    queue_column: str | None = None
    queue_work_values: frozenset[str] = frozenset()

    # Where the runner stamps the processing date. On a tab whose queue
    # formula reads this column it MUST already exist (list it in
    # extra_required_columns) — auto-creating a second one would leave the
    # formula's cell forever blank and re-queue every row.
    timestamp_column: str = "Last Checked"

    # Headers that must already exist in the sheet; never auto-created.
    extra_required_columns: list[str] = []

    # The header the runner writes this worker's route() output into
    # (VC: "Recommended_Channel"; grants: "Recommended_Action").
    route_column: str

    # Worker-specific bot-owned columns beyond CORE_COLUMNS + route_column,
    # auto-created if missing.
    extra_columns: list[str] = []

    # Optional cheap-recheck path: when a row is re-queued because it went
    # STALE (not because it's brand new), a full re-research of every field
    # is wasteful — most of it hasn't changed. If recheck_schema is set,
    # the runner uses build_recheck_prompt()/compute_recheck_updates()
    # instead of the full research path for STALE rows. None (the default)
    # means every row gets full research regardless of why it was queued.
    recheck_schema: type[BaseModel] | None = None

    def __init__(self) -> None:
        never_write = set(self.human_owned_columns) | set(self.formula_columns)
        reserved = {"Bot_Status", self.timestamp_column, self.route_column}

        overlap = set(self.column_map.values()) & never_write
        if overlap:
            raise ValueError(
                f"{self.name}: column_map may never target human-owned or "
                f"formula columns: {sorted(overlap)}"
            )
        overlap = set(self.column_map.values()) & reserved
        if overlap:
            raise ValueError(
                f"{self.name}: column_map may never target runner-managed "
                f"columns: {sorted(overlap)}"
            )
        overlap = set(self.sentinel_columns) - set(self.column_map.values())
        if overlap:
            raise ValueError(
                f"{self.name}: sentinel_columns must all be column_map "
                f"targets, but these are not: {sorted(overlap)}"
            )

    @property
    def owned_columns(self) -> list[str]:
        """Bot-owned columns: auto-created (appended right of all existing
        data) if missing. Never includes a column listed as required."""
        required = set(self.extra_required_columns)
        seen: dict[str, None] = {}
        for header in [
            *CORE_COLUMNS,
            self.route_column,
            self.timestamp_column,
            *self.extra_columns,
        ]:
            if header not in required:
                seen.setdefault(header, None)
        return list(seen)

    @property
    def required_columns(self) -> list[str]:
        """Headers that must already exist. Business columns a worker writes
        into are real sheet columns, not something it invents for itself."""
        owned = set(self.owned_columns)
        seen: dict[str, None] = {}
        for header in [
            *self.input_columns,
            *self.extra_required_columns,
            *(h for h in self.column_map.values() if h not in owned),
        ]:
            seen.setdefault(header, None)
        if self.queue_column:
            seen.setdefault(self.queue_column, None)
        return list(seen)

    def configure(self, settings: Any) -> None:
        """Apply env-driven overrides (e.g. a renamed queue column) before
        the run starts. Default: nothing to configure."""

    def wants_row(self, row: dict[str, str]) -> bool:
        """Does the sheet's queue column say to process this row?"""
        if not self.queue_column:
            return True
        value = (row.get(self.queue_column) or "").strip().casefold()
        return value in {v.strip().casefold() for v in self.queue_work_values}

    def wants_recheck(self, row: dict[str, str]) -> bool:
        """Was this row queued because it went STALE (already researched,
        just due for a refresh) rather than because it's brand new? Only
        meaningful when recheck_schema is set — the runner checks that
        separately, so this only needs to read the queue value itself."""
        if self.recheck_schema is None or not self.queue_column:
            return False
        value = (row.get(self.queue_column) or "").strip().casefold()
        return value == "stale"

    @abstractmethod
    def build_prompt(self, row: dict[str, str], settings: Any) -> str:
        """Render the worker's prompt template using this row's input
        columns and worker-level config (e.g. company context)."""

    @abstractmethod
    def route(self, result: BaseModel) -> str:
        """Deterministic routing/action recommendation from a validated
        result, written into `route_column`. Must not perform I/O."""

    def page_hint(self, row: dict[str, str]) -> str | None:
        """A URL already known for this row, worth fetching directly before
        paying for web search. None disables the cheap path."""
        return None

    def is_extraction_sufficient(self, result: BaseModel) -> bool:
        """Did the cheap fetch-then-extract path find enough to skip web
        search? Default: no, always fall back."""
        return False

    def build_recheck_prompt(self, row: dict[str, str], settings: Any) -> str:
        """Only called when recheck_schema is set and the row is a STALE
        recheck rather than a fresh row. No default — a worker that sets
        recheck_schema must implement this."""
        raise NotImplementedError(f"{self.name} sets recheck_schema but has no build_recheck_prompt")

    def compute_recheck_updates(
        self, result: BaseModel, row: dict[str, str], force_refresh: bool
    ) -> dict[str, str]:
        """Map a recheck result onto sheet headers. Only called when
        recheck_schema is set. No default — a worker that sets
        recheck_schema must implement this."""
        raise NotImplementedError(f"{self.name} sets recheck_schema but has no compute_recheck_updates")

    def compute_business_updates(
        self, result: BaseModel, row: dict[str, str], force_refresh: bool
    ) -> dict[str, str]:
        """Map a validated result onto sheet headers.

        Two rules interact here:
        - Never overwrite a non-empty cell unless --force-refresh. A cell
          holding the sentinel counts as filled.
        - Every sentinel column ends up non-blank, so the sheet's queue
          formula stops reporting this row as unresearched.
        """
        updates: dict[str, str] = {}
        for field_name, header in self.column_map.items():
            value = getattr(result, field_name)
            is_sentinel_column = header in self.sentinel_columns
            currently_blank = not (row.get(header) or "").strip()

            if not (
                field_name in self.always_overwrite_fields
                or currently_blank
                or force_refresh
            ):
                continue

            if value is None or (isinstance(value, str) and not value.strip()):
                if is_sentinel_column:
                    updates[header] = SENTINEL_VALUE
                continue

            updates[header] = format_value(value)
        return updates

    def after_research(
        self,
        row: dict[str, str],
        result: BaseModel,
        route_value: str,
        settings: Any,
    ) -> dict[str, str]:
        """Optional hook for worker-specific side effects run after the
        standard columns are computed. Returns extra header -> value writes
        to merge into the row update. Default: no-op.
        """
        return {}
