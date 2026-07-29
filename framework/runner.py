"""Generic, worker-agnostic queue-processing loop. Nothing here may import
from a specific worker's module — new workers plug in purely via the
Worker contract (framework/worker_base.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from pydantic import BaseModel

from framework.logging import get_logger, log_event
from framework.validation import (
    SchemaValidationError,
    build_retry_prompt,
    parse_and_validate,
)
from framework.worker_base import (
    BOT_STATUS_ERROR,
    BOT_STATUS_NEEDS_REVIEW,
    BOT_STATUS_QUEUED_EXPLICIT,
    BOT_STATUS_RESEARCHING,
    TERMINAL_BOT_STATUSES,
    Worker,
)
from sheets.client import ROW_NUMBER_KEY

_MAX_ATTEMPTS = 3  # initial call + 2 retries
_NOTES_TRUNCATE = 500

logger = get_logger("utilyze.runner")


@dataclass
class RunnerOptions:
    batch_size: int
    force_refresh: bool = False
    requeue: bool = False
    dry_run: bool = False


@dataclass
class RunSummary:
    processed: int = 0
    needs_review: int = 0
    errors: int = 0
    skipped: int = 0
    queue_size: int = 0
    rows: list[int] = field(default_factory=list)


def select_queue(
    rows: list[dict[str, str]], worker: Worker, options: RunnerOptions
) -> list[dict[str, str]]:
    allowed_statuses = {"", BOT_STATUS_QUEUED_EXPLICIT}
    if options.requeue:
        allowed_statuses |= TERMINAL_BOT_STATUSES

    business_headers = [
        header
        for field_name, header in worker.column_map.items()
        if field_name not in worker.always_overwrite_fields
    ]

    queue = []
    for row in rows:
        status = row.get("Bot_Status", "")
        if status not in allowed_statuses:
            continue
        if not options.force_refresh:
            has_blank = any(not row.get(header) for header in business_headers)
            if not has_blank:
                continue
        queue.append(row)
    return queue


def _build_success_updates(
    row: dict[str, str],
    result: BaseModel,
    worker: Worker,
    settings: Any,
    options: RunnerOptions,
    today: str,
) -> tuple[dict[str, str], str]:
    updates = worker.compute_business_updates(result, row, options.force_refresh)

    confidence = getattr(result, "confidence")
    notes = updates.get("Research_Notes", "") or ""
    if confidence < settings.confidence_min:
        bot_status = BOT_STATUS_NEEDS_REVIEW
        low_conf_flag = f"[LOW CONFIDENCE: {confidence:.2f}] "
        if not notes.startswith(low_conf_flag):
            notes = low_conf_flag + notes
        updates["Research_Notes"] = notes
    else:
        bot_status = BOT_STATUS_NEEDS_REVIEW

    route_value = worker.route(result)

    updates[worker.route_column] = route_value
    updates["Bot_Status"] = bot_status
    updates["Last_Checked"] = today

    extra = worker.after_research(row, result, route_value, settings)
    updates.update(extra)

    return updates, bot_status


def _assert_no_human_owned_writes(updates: dict[str, str], worker: Worker) -> None:
    violations = set(updates) & set(worker.human_owned_columns)
    if violations:
        raise AssertionError(f"Refusing to write human-owned columns: {violations}")


def _write(
    sheets_client: Any, worker: Worker, row_number: int, updates: dict[str, str], dry_run: bool
) -> None:
    _assert_no_human_owned_writes(updates, worker)
    if not updates:
        return
    if dry_run:
        log_event(logger, "dry_run_write", row=row_number, updates=updates)
        return
    sheets_client.write_cells(row_number, updates)


def _process_row(
    row: dict[str, str],
    worker: Worker,
    sheets_client: Any,
    llm_client: Any,
    settings: Any,
    options: RunnerOptions,
    today: str,
) -> str:
    row_number = row[ROW_NUMBER_KEY]

    _write(
        sheets_client,
        worker,
        row_number,
        {"Bot_Status": BOT_STATUS_RESEARCHING, "Last_Checked": today},
        options.dry_run,
    )

    prompt = worker.build_prompt(row, settings)
    errors: list[str] = []
    result: BaseModel | None = None

    current_prompt = prompt
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        raw = llm_client.research(current_prompt)
        try:
            result = parse_and_validate(raw, worker.output_schema)
            break
        except SchemaValidationError as exc:
            errors.append(str(exc))
            current_prompt = build_retry_prompt(prompt, exc)

    if result is None:
        reason = " | ".join(errors)[:_NOTES_TRUNCATE]
        _write(
            sheets_client,
            worker,
            row_number,
            {
                "Bot_Status": BOT_STATUS_ERROR,
                "Last_Checked": today,
                "Research_Notes": reason,
            },
            options.dry_run,
        )
        log_event(logger, "row_error", row=row_number, reason=reason)
        return BOT_STATUS_ERROR

    updates, bot_status = _build_success_updates(row, result, worker, settings, options, today)
    _write(sheets_client, worker, row_number, updates, options.dry_run)
    log_event(logger, "row_processed", row=row_number, bot_status=bot_status)
    return bot_status


def run(
    worker: Worker,
    sheets_client: Any,
    llm_client: Any,
    settings: Any,
    options: RunnerOptions,
) -> RunSummary:
    owned = set(worker.owned_columns)
    sheets_client.ensure_columns(worker.owned_columns)
    sheets_client.require_columns(worker.input_columns)
    sheets_client.require_columns([h for h in worker.column_map.values() if h not in owned])

    rows = sheets_client.read_all_rows()
    queue = select_queue(rows, worker, options)
    batch = queue[: options.batch_size]

    today = date.today().isoformat()
    summary = RunSummary(queue_size=len(queue), skipped=len(rows) - len(queue))

    for row in batch:
        outcome = _process_row(row, worker, sheets_client, llm_client, settings, options, today)
        summary.rows.append(row[ROW_NUMBER_KEY])
        if outcome == BOT_STATUS_ERROR:
            summary.errors += 1
        else:
            summary.processed += 1
            summary.needs_review += 1

    log_event(
        logger,
        "run_summary",
        processed=summary.processed,
        needs_review=summary.needs_review,
        errors=summary.errors,
        skipped=summary.skipped,
        queue_size=summary.queue_size,
    )
    return summary
