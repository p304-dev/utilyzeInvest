"""The grants worker: input columns, schema, column map, prompt building,
and fit/eligibility-based action routing for the Grants / Pitches tab.

Research-only — no channel routing to a person, no drafting to a send
step. Reuses the entire framework (runner, Sheets client, validation)
unchanged; only the schema, column map, scoring, and prompt differ from
vc_research.

Unlike the Investors tab, Grants / Pitches has no queue formula column, so
this worker leaves `queue_column` unset and the runner falls back to
scanning for blank business columns. The sentinel rule is likewise not
applied here: without a COUNTBLANK-driven formula there is nothing to
satisfy, and writing a sentinel into empty cells would only add noise.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from framework.worker_base import Worker, format_value
from workers.grants.schema import GrantsResult

_PROMPT_PATH = Path(__file__).parent / "prompt.md"

ACTION_APPLY = "Apply"
ACTION_WATCH = "Watch"
ACTION_SKIP = "Skip"

_MIN_FIT_SCORE_TO_APPLY = 70

# Best-effort, dependency-free parse of a handful of common deadline
# formats. A deadline we can't confidently parse (or "Rolling", or blank)
# is treated as feasible rather than guessed at as passed.
_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y")


def _deadline_passed(deadline: str | None, today: date) -> bool:
    if not deadline:
        return False
    normalized = deadline.strip()
    if "rolling" in normalized.lower():
        return False
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(normalized, fmt).date()
        except ValueError:
            continue
        return parsed < today
    return False


class GrantsWorker(Worker):
    name = "grants"
    sheet_tab = "Grants / Pitches"
    input_columns = ["Name", "Website or Application Link", "Category"]
    output_schema = GrantsResult

    # Only the plain 1:1 business fields go through the standard column_map
    # walk (blank-cell-only unless --force-refresh). Everything else —
    # Eligibility, Requirements, Research_Notes, Fit_Score, Draft_Outline —
    # is composite/always-refreshed and handled in
    # compute_business_updates() below.
    column_map = {
        "category": "Category",
        "deadline": "Deadline",
        "contact_email": "Email",
    }

    human_owned_columns = ["Status", "Name", "Website or Application Link"]
    route_column = "Recommended_Action"
    extra_columns = ["Fit_Score", "Eligibility", "Requirements", "Draft_Outline"]

    def build_prompt(self, row: dict[str, str], settings: Any) -> str:
        template = _PROMPT_PATH.read_text(encoding="utf-8")
        return (
            template.replace("{{UTILYZE_CONTEXT}}", settings.utilyze_context)
            .replace("{{Name}}", row.get("Name", ""))
            .replace("{{Website or Application Link}}", row.get("Website or Application Link", ""))
            .replace("{{Category}}", row.get("Category", ""))
        )

    def route(self, result: BaseModel) -> str:
        assert isinstance(result, GrantsResult)
        if result.eligible != "Eligible" or _deadline_passed(result.deadline, date.today()):
            return ACTION_SKIP
        if result.fit_score >= _MIN_FIT_SCORE_TO_APPLY:
            return ACTION_APPLY
        return ACTION_WATCH

    def compute_business_updates(
        self, result: BaseModel, row: dict[str, str], force_refresh: bool
    ) -> dict[str, str]:
        assert isinstance(result, GrantsResult)
        updates: dict[str, str] = {}

        def fill_if_blank(header: str, value: Any) -> None:
            if value is None:
                return
            if not row.get(header) or force_refresh:
                updates[header] = format_value(value)

        fill_if_blank("Category", result.category)
        fill_if_blank("Deadline", result.deadline)
        fill_if_blank("Email", result.contact_email)

        # Worker's own assessment — always refreshed on every processed row.
        updates["Fit_Score"] = format_value(result.fit_score)
        updates["Draft_Outline"] = result.draft_outline
        updates["Confidence"] = format_value(result.confidence)
        updates["Source_URLs"] = format_value(result.source_urls)

        eligibility = result.eligible
        if result.eligibility_reason:
            eligibility = f"{eligibility} — {result.eligibility_reason}"
        updates["Eligibility"] = eligibility

        if result.requirements:
            updates["Requirements"] = "; ".join(result.requirements)

        notes_parts = [part for part in (result.fit_rationale, result.notes) if part]
        if result.application_link:
            notes_parts.append(f"Application link: {result.application_link}")
        if notes_parts:
            updates["Research_Notes"] = " | ".join(notes_parts)

        return updates
