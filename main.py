"""CLI entry point for the Utilyze automation workers.

    python main.py --worker vc_research [--batch N] [--force-refresh] [--requeue] [--dry-run]
    python main.py --worker vc_research --publish [--dry-run]
"""

from __future__ import annotations

import argparse
import sys

from config.settings import get_settings
from framework.logging import get_logger, log_event
from framework.runner import RunnerOptions, run
from framework.worker_base import Worker
from llm.client import LLMClient
from sheets.client import SheetsClient
from workers.grants.worker import GrantsWorker
from workers.vc_research.worker import VCResearchWorker

WORKER_REGISTRY: dict[str, type[Worker]] = {
    "vc_research": VCResearchWorker,
    "grants": GrantsWorker,
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Utilyze automation worker runner")
    parser.add_argument(
        "--worker", required=True, choices=sorted(WORKER_REGISTRY), help="Which worker to run"
    )
    parser.add_argument("--batch", type=int, default=None, help="Override BATCH_SIZE for this run")
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Process regardless of the queue column, overwriting filled business cells",
    )
    parser.add_argument(
        "--requeue",
        action="store_true",
        help="Include rows previously marked Error",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log intended writes without touching the sheet or Wix",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Publish researched rows to Wix instead of researching. Never runs research.",
    )
    return parser.parse_args(argv)


def _publish(worker: Worker, settings, logger, dry_run: bool) -> int:
    from wix.publish import build_client, publish_rows

    sheets_client = SheetsClient.from_service_account(
        credentials_path=settings.google_application_credentials,
        spreadsheet_id=settings.sheet_id,
        sheet_tab=worker.sheet_tab,
    )
    if not worker.queue_column:
        log_event(
            logger,
            "publish_unsupported",
            worker=worker.name,
            reason="worker has no queue column to decide what is publishable",
            level="ERROR",
        )
        return 1

    rows = sheets_client.read_all_rows()
    summary = publish_rows(
        rows,
        build_client(settings),
        queue_column=worker.queue_column,
        dry_run=dry_run,
    )
    log_event(
        logger,
        "publish_complete",
        worker=worker.name,
        published=summary.published,
        skipped=summary.skipped,
        errors=summary.errors,
    )
    return 1 if summary.errors else 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    logger = get_logger("utilyze.main", level=settings.log_level)

    worker = WORKER_REGISTRY[args.worker]()
    worker.configure(settings)

    if args.publish:
        return _publish(worker, settings, logger, args.dry_run)

    sheets_client = SheetsClient.from_service_account(
        credentials_path=settings.google_application_credentials,
        spreadsheet_id=settings.sheet_id,
        sheet_tab=worker.sheet_tab,
    )
    llm_client = LLMClient(
        api_key=settings.anthropic_api_key,
        model=settings.anthropic_model,
        effort=settings.anthropic_effort,
        max_tokens=settings.anthropic_max_tokens,
        web_search_max_uses=settings.web_search_max_uses,
    )

    options = RunnerOptions(
        batch_size=args.batch if args.batch is not None else settings.batch_size,
        force_refresh=args.force_refresh,
        requeue=args.requeue,
        dry_run=args.dry_run,
    )

    summary = run(worker, sheets_client, llm_client, settings, options)

    log_event(
        logger,
        "run_complete",
        worker=worker.name,
        processed=summary.processed,
        errors=summary.errors,
        skipped=summary.skipped,
        queue_size=summary.queue_size,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
