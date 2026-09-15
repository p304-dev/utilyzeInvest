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
    errors: int = 0
    skipped: int = 0
    queue_size: int = 0
    rows: list[int] = field(default_factory=list)


def select_queue(
    rows: list[dict[str, str]], worker: Worker, options: RunnerOptions
) -> list[dict[str, str]]:
    """Read the queue decision; don't recompute it.

    On a tab with a queue formula (Investors' column Q), the spreadsheet
    owns the decision: it reports a row as needing work until the worker
    fills the scanned columns and stamps the timestamp, then the row leaves
    the queue on its own. Rows that previously errored are held back so a
    hard failure doesn't burn budget every run.
    """
    queue: list[dict[str, str]] = []
    for row in rows:
        if row.get("Bot_Status") == BOT_STATUS_ERROR and not options.requeue:
            continue
        if not options.force_refresh and not worker.wants_row(row):
            continue
        if worker.queue_column is None and not options.force_refresh:
            # No queue formula on this tab (grants): fall back to scanning
            # for a business column the worker could still fill.
            business_headers = [
                header
                for field_name, header in worker.column_map.items()
                if field_name not in worker.always_overwrite_fields
            ]
            if not any(not row.get(header) for header in business_headers):
                continue
        queue.append(row)
    return queue


def _flag_low_confidence(updates: dict[str, str], confidence: float, settings: Any) -> None:
    if confidence < settings.confidence_min:
        notes = updates.get("Research_Notes", "") or ""
        low_conf_flag = f"[LOW CONFIDENCE: {confidence:.2f}] "
        if not notes.startswith(low_conf_flag):
            updates["Research_Notes"] = low_conf_flag + notes


def _build_success_updates(
    row: dict[str, str],
    result: BaseModel,
    worker: Worker,
    settings: Any,
    options: RunnerOptions,
    today: str,
) -> tuple[dict[str, str], str]:
    updates = worker.compute_business_updates(result, row, options.force_refresh)
    _flag_low_confidence(updates, getattr(result, "confidence"), settings)

    route_value = worker.route(result)

    updates[worker.route_column] = route_value
    updates["Bot_Status"] = BOT_STATUS_NEEDS_REVIEW
    updates[worker.timestamp_column] = today

    updates.update(worker.after_research(row, result, route_value, settings))

    return updates, BOT_STATUS_NEEDS_REVIEW


def _build_recheck_updates(
    row: dict[str, str],
    result: BaseModel,
    worker: Worker,
    settings: Any,
    options: RunnerOptions,
    today: str,
) -> tuple[dict[str, str], str]:
    """A recheck only ever re-verifies a handful of fields (see the
    worker's compute_recheck_updates) — it never recomputes routing or
    triggers after_research(), since those depend on the full contact
    picture a recheck doesn't re-derive. Whatever channel/drafts a full
    research pass already produced are left exactly as they are."""
    updates = worker.compute_recheck_updates(result, row, options.force_refresh)
    _flag_low_confidence(updates, getattr(result, "confidence"), settings)

    updates["Bot_Status"] = BOT_STATUS_NEEDS_REVIEW
    updates[worker.timestamp_column] = today

    return updates, BOT_STATUS_NEEDS_REVIEW


def _assert_writable(updates: dict[str, str], worker: Worker) -> None:
    violations = set(updates) & (
        set(worker.human_owned_columns) | set(worker.formula_columns)
    )
    if violations:
        raise AssertionError(
            f"Refusing to write human-owned or formula columns: {sorted(violations)}"
        )


def _write(
    sheets_client: Any, worker: Worker, row_number: int, updates: dict[str, str], dry_run: bool
) -> None:
    _assert_writable(updates, worker)
    if not updates:
        return
    if dry_run:
        log_event(logger, "dry_run_write", row=row_number, updates=updates)
        return
    sheets_client.write_cells(row_number, updates)


def _assert_sentinels_filled(updates: dict[str, str], row: dict[str, str], worker: Worker) -> None:
    """Every scanned column must be non-blank once a row is processed, or
    the queue formula reports it as unresearched forever."""
    unfilled = [
        header
        for header in worker.sentinel_columns
        if not (updates.get(header) or row.get(header) or "").strip()
    ]
    if unfilled:
        raise AssertionError(
            f"Sentinel columns left blank (row would re-queue forever): {sorted(unfilled)}"
        )


def _try_cheap_extract(
    row: dict[str, str],
    worker: Worker,
    llm_client: Any,
    settings: Any,
    prompt: str,
) -> BaseModel | None:
    """Fetch the row's own website and extract from that text instead of
    paying for web search. Returns None whenever the cheap path isn't
    applicable or didn't yield enough, so the caller falls back."""
    if not getattr(settings, "fetch_first", False):
        return None
    url = worker.page_hint(row)
    if not url:
        return None

    from llm.fetch import fetch_page_text

    page_text = fetch_page_text(
        url,
        timeout=getattr(settings, "fetch_timeout_seconds", 15.0),
        max_chars=getattr(settings, "fetch_max_chars", 20_000),
    )
    if not page_text:
        return None

    try:
        result = parse_and_validate(llm_client.extract(prompt, page_text), worker.output_schema)
    except SchemaValidationError as exc:
        log_event(logger, "cheap_extract_invalid", url=url, reason=str(exc)[:200], level="WARNING")
        return None

    if not worker.is_extraction_sufficient(result):
        log_event(logger, "cheap_extract_thin", url=url)
        return None
    return result


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
    is_recheck = worker.wants_recheck(row)

    if is_recheck:
        schema = worker.recheck_schema
        prompt = worker.build_recheck_prompt(row, settings)
        result: BaseModel | None = None  # cheap fetch-first targets output_schema, not this
    else:
        schema = worker.output_schema
        prompt = worker.build_prompt(row, settings)
        result = _try_cheap_extract(row, worker, llm_client, settings, prompt)

    errors: list[str] = []
    current_prompt = prompt
    for _attempt in range(_MAX_ATTEMPTS):
        if result is not None:
            break
        raw = llm_client.research(current_prompt)
        try:
            result = parse_and_validate(raw, schema)
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
                worker.timestamp_column: today,
                "Research_Notes": reason,
            },
            options.dry_run,
        )
        log_event(logger, "row_error", row=row_number, reason=reason, recheck=is_recheck)
        return BOT_STATUS_ERROR

    if is_recheck:
        updates, bot_status = _build_recheck_updates(row, result, worker, settings, options, today)
    else:
        updates, bot_status = _build_success_updates(row, result, worker, settings, options, today)
    _assert_sentinels_filled(updates, row, worker)
    _write(sheets_client, worker, row_number, updates, options.dry_run)
    log_event(logger, "row_processed", row=row_number, bot_status=bot_status, recheck=is_recheck)
    return bot_status


def assert_no_owned_column_in_scanned_range(
    sheets_client: Any, worker: Worker, first: int, last: int
) -> None:
    """Bot-owned columns must never land inside the range the sheet's queue
    formula scans (Investors: D:O). A column inserted there silently changes
    which rows the formula considers unresearched."""
    if not worker.sentinel_columns:
        return
    intruders = [
        header
        for header in worker.owned_columns
        if sheets_client.has_column(header)
        and first <= sheets_client.column_index(header) <= last
    ]
    if intruders:
        raise AssertionError(
            f"Bot-owned column(s) inside the formula-scanned range "
            f"(columns {first}-{last}): {sorted(intruders)}"
        )


def run(
    worker: Worker,
    sheets_client: Any,
    llm_client: Any,
    settings: Any,
    options: RunnerOptions,
) -> RunSummary:
    sheets_client.require_columns(worker.required_columns)
    sheets_client.ensure_columns(worker.owned_columns)
    assert_no_owned_column_in_scanned_range(sheets_client, worker, first=4, last=15)

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

    log_event(
        logger,
        "run_summary",
        worker=worker.name,
        processed=summary.processed,
        errors=summary.errors,
        skipped=summary.skipped,
        queue_size=summary.queue_size,
    )
    return summary
