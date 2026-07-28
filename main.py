"""CLI entry point for the Utilyze automation workers.

    python main.py --worker vc_research [--batch N] [--force-refresh] [--requeue] [--dry-run]
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
from workers.vc_research.worker import VCResearchWorker

WORKER_REGISTRY: dict[str, type[Worker]] = {
    "vc_research": VCResearchWorker,
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
        help="Overwrite non-blank worker-writable business cells",
    )
    parser.add_argument(
        "--requeue",
        action="store_true",
        help="Re-process rows already marked Needs Review or Error",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log intended writes without touching the sheet",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    logger = get_logger("utilyze.main", level=settings.log_level)

    worker = WORKER_REGISTRY[args.worker]()

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
        needs_review=summary.needs_review,
        errors=summary.errors,
        skipped=summary.skipped,
        queue_size=summary.queue_size,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
