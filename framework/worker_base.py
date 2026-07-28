"""The Worker contract. Every worker (vc_research, and future ones like
grants) implements this; framework/runner.py only ever depends on this
interface, never on a specific worker's schema or columns.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel

# Columns the runner itself owns and manages for ANY worker. Created
# automatically if missing (see sheets/client.py: ensure_columns).
FRAMEWORK_COLUMNS: list[str] = [
    "Bot_Status",
    "Recommended_Channel",
    "Confidence",
    "Source_URLs",
    "Research_Notes",
    "Last_Checked",
    "Draft_Subject",
    "Draft_Body",
    "Draft_Status",
    "Draft_Link",
]

# The subset of FRAMEWORK_COLUMNS the runner computes/writes itself
# (queue state, routing output, timestamp, after_research() outputs) —
# never sourced from a worker's column_map. Confidence/Source_URLs/
# Research_Notes/Draft_Subject/Draft_Body are deliberately NOT in this
# set: those come straight from schema fields via column_map.
RUNNER_MANAGED_COLUMNS: list[str] = [
    "Bot_Status",
    "Recommended_Channel",
    "Last_Checked",
    "Draft_Status",
    "Draft_Link",
]

# Columns no worker may ever write to, under any flag. A human owns these.
HUMAN_OWNED_COLUMNS: list[str] = [
    "Status",
    "Contact Date",
    "Found",
]

BOT_STATUS_QUEUED = ""
BOT_STATUS_QUEUED_EXPLICIT = "Queued"
BOT_STATUS_RESEARCHING = "Researching"
BOT_STATUS_NEEDS_REVIEW = "Needs Review"
BOT_STATUS_ERROR = "Error"

# Bot_Status values that mark a row as already handled; excluded from the
# queue unless --requeue is passed.
TERMINAL_BOT_STATUSES: set[str] = {BOT_STATUS_NEEDS_REVIEW, BOT_STATUS_ERROR}


class Worker(ABC):
    """Contract a concrete worker (e.g. vc_research) must implement."""

    name: str
    sheet_tab: str
    input_columns: list[str]
    output_schema: type[BaseModel]
    # pydantic field name -> sheet header. Must never contain a key that
    # collides with FRAMEWORK_COLUMNS or HUMAN_OWNED_COLUMNS.
    column_map: dict[str, str]

    # Subset of column_map keys that represent the worker's own assessment
    # (confidence, source_urls, notes, draft copy, ...) rather than
    # ground-truth business data a human might also fill in by hand. These
    # are overwritten every time a row is processed, regardless of
    # --force-refresh. Everything else in column_map is written only to a
    # blank cell unless --force-refresh is passed.
    always_overwrite_fields: frozenset[str] = frozenset()

    def __init__(self) -> None:
        overlap = set(self.column_map.values()) & set(HUMAN_OWNED_COLUMNS)
        if overlap:
            raise ValueError(
                f"{self.name}: column_map may never target human-owned "
                f"columns: {sorted(overlap)}"
            )
        overlap = set(self.column_map.values()) & set(RUNNER_MANAGED_COLUMNS)
        if overlap:
            raise ValueError(
                f"{self.name}: column_map may never target runner-managed "
                f"columns: {sorted(overlap)}"
            )

    @abstractmethod
    def build_prompt(self, row: dict[str, str], settings: Any) -> str:
        """Render the worker's prompt template using this row's input
        columns and worker-level config (e.g. company context)."""

    @abstractmethod
    def route(self, result: BaseModel) -> str:
        """Deterministic channel/action recommendation from a validated
        result. Must not perform I/O."""

    def after_research(
        self,
        row: dict[str, str],
        result: BaseModel,
        channel: str,
        settings: Any,
    ) -> dict[str, str]:
        """Optional hook for worker-specific side effects (e.g. VC's Gmail
        draft creation) run after the standard columns are computed.
        Returns extra header -> value writes to merge into the row update.
        Default: no-op.
        """
        return {}
