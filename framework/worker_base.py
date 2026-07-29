"""The Worker contract. Every worker (vc_research, grants, and future ones)
implements this; framework/runner.py only ever depends on this interface,
never on a specific worker's schema, columns, or sheet layout.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel

# Columns every worker shares, regardless of sheet/tab: queue state,
# worker-assessed confidence/sources/notes, and the last-processed
# timestamp. Always auto-created (see sheets/client.py: ensure_columns).
# Confidence/Source_URLs/Research_Notes are ordinarily populated via a
# worker's column_map / compute_business_updates() (they hold the LLM's
# own assessment); Bot_Status and Last_Checked are the two the runner
# always writes directly (see RUNNER_DIRECT_COLUMNS below).
CORE_COLUMNS: list[str] = [
    "Bot_Status",
    "Confidence",
    "Source_URLs",
    "Research_Notes",
    "Last_Checked",
]

# The subset of CORE_COLUMNS (plus each worker's route_column) the runner
# writes directly, never via column_map / compute_business_updates() — a
# worker's column_map may never target these.
RUNNER_DIRECT_COLUMNS: list[str] = ["Bot_Status", "Last_Checked"]

BOT_STATUS_QUEUED = ""
BOT_STATUS_QUEUED_EXPLICIT = "Queued"
BOT_STATUS_RESEARCHING = "Researching"
BOT_STATUS_NEEDS_REVIEW = "Needs Review"
BOT_STATUS_ERROR = "Error"

# Bot_Status values that mark a row as already handled; excluded from the
# queue unless --requeue is passed.
TERMINAL_BOT_STATUSES: set[str] = {BOT_STATUS_NEEDS_REVIEW, BOT_STATUS_ERROR}


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
    # blank cell unless --force-refresh is passed. Ignored by workers that
    # override compute_business_updates().
    always_overwrite_fields: frozenset[str] = frozenset()

    # Columns a human owns on this worker's sheet/tab — the worker must
    # never write to them, under any flag. Always includes "Status" at
    # minimum; varies by sheet (see each worker's ground-truth columns).
    human_owned_columns: list[str]

    # The header the runner writes this worker's route() output into
    # (VC: "Recommended_Channel"; grants: "Recommended_Action").
    route_column: str

    # Worker-specific new columns beyond CORE_COLUMNS + route_column that
    # must be auto-created if missing.
    extra_columns: list[str] = []

    def __init__(self) -> None:
        reserved = set(RUNNER_DIRECT_COLUMNS) | {self.route_column}

        overlap = set(self.column_map.values()) & set(self.human_owned_columns)
        if overlap:
            raise ValueError(
                f"{self.name}: column_map may never target human-owned "
                f"columns: {sorted(overlap)}"
            )
        overlap = set(self.column_map.values()) & reserved
        if overlap:
            raise ValueError(
                f"{self.name}: column_map may never target runner-managed "
                f"columns: {sorted(overlap)}"
            )

    @property
    def owned_columns(self) -> list[str]:
        """All columns this worker's rows own: created via ensure_columns()
        if missing, never required to pre-exist."""
        seen: dict[str, None] = {}
        for header in [*CORE_COLUMNS, self.route_column, *self.extra_columns]:
            seen.setdefault(header, None)
        return list(seen)

    @abstractmethod
    def build_prompt(self, row: dict[str, str], settings: Any) -> str:
        """Render the worker's prompt template using this row's input
        columns and worker-level config (e.g. company context)."""

    @abstractmethod
    def route(self, result: BaseModel) -> str:
        """Deterministic routing/action recommendation from a validated
        result, written into `route_column`. Must not perform I/O."""

    def compute_business_updates(
        self, result: BaseModel, row: dict[str, str], force_refresh: bool
    ) -> dict[str, str]:
        """Map a validated result onto sheet headers, respecting the
        blank-cell-only-unless-force-refresh rule. Default: a straight walk
        over column_map. Override for composite/joined columns."""
        updates: dict[str, str] = {}
        for field_name, header in self.column_map.items():
            value = getattr(result, field_name)
            if value is None:
                continue
            always_overwrite = field_name in self.always_overwrite_fields
            currently_blank = not row.get(header)
            if always_overwrite or currently_blank or force_refresh:
                updates[header] = format_value(value)
        return updates

    def after_research(
        self,
        row: dict[str, str],
        result: BaseModel,
        route_value: str,
        settings: Any,
    ) -> dict[str, str]:
        """Optional hook for worker-specific side effects (e.g. VC's Gmail
        draft creation) run after the standard columns are computed.
        Returns extra header -> value writes to merge into the row update.
        Default: no-op.
        """
        return {}
