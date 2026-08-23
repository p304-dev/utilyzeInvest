from __future__ import annotations

import json

from config.settings import Settings
from framework.runner import RunnerOptions, run
from tests.fixtures.fake_llm import FakeLLMClient
from tests.fixtures.fake_sheets import FakeSheetsClient
from workers.grants.worker import GrantsWorker

BASE_HEADERS = [
    "Status",
    "Name",
    "Email",
    "Deadline",
    "Category",
    "Website or Application Link",
]


def _base_row(**overrides) -> dict[str, str]:
    row = {header: "" for header in BASE_HEADERS}
    row["Name"] = "Utility Innovation Grant"
    row["Website or Application Link"] = "https://example.org/apply"
    row.update(overrides)
    return row


def _run_once(rows, response_json, force_refresh=False):
    sheet = FakeSheetsClient(rows, headers=list(BASE_HEADERS))
    llm = FakeLLMClient([response_json])
    worker = GrantsWorker()
    settings = Settings(_env_file=None, fetch_first=False)
    options = RunnerOptions(batch_size=5, force_refresh=force_refresh)
    run(worker, sheet, llm, settings, options)
    return sheet


def _response(**overrides) -> str:
    base = {
        "name": "Utility Innovation Grant",
        "category": "Grant",
        "eligible": "Eligible",
        "eligibility_reason": "For-profit utilities startup, US-based",
        "deadline": None,
        "contact_email": None,
        "application_link": "https://example.org/apply-now",
        "requirements": ["Pitch deck", "Letter of intent"],
        "fit_score": 55,
        "fit_rationale": "Good domain fit, moderate award value",
        "recommended_action": "Watch",
        "draft_outline": "1. Company overview...",
        "confidence": 0.85,
        "source_urls": ["https://example.org"],
        "notes": None,
    }
    base.update(overrides)
    return json.dumps(base)


def test_grants_worker_column_map_never_targets_human_owned_or_direct_columns():
    worker = GrantsWorker()
    reserved = {"Bot_Status", worker.timestamp_column, worker.route_column}
    assert not (set(worker.column_map.values()) & set(worker.human_owned_columns))
    assert not (set(worker.column_map.values()) & reserved)


def test_grants_has_no_queue_formula_and_no_sentinels():
    # The Grants / Pitches tab has no COUNTBLANK-driven queue column, so
    # this worker keeps the blank-scanning fallback and writes no sentinels.
    worker = GrantsWorker()
    assert worker.queue_column is None
    assert worker.sentinel_columns == frozenset()


def test_grants_runner_never_writes_human_owned_columns():
    rows = [_base_row()]
    sheet = _run_once(rows, _response())
    assert sheet.write_calls
    for _row_number, updates in sheet.write_calls:
        assert "Status" not in updates
        assert "Name" not in updates
        assert "Website or Application Link" not in updates


def test_grants_runner_only_fills_blank_business_columns_by_default():
    rows = [_base_row(Category="Pitch Competition")]
    sheet = _run_once(rows, _response(category="Incubator"))
    final_row = sheet.rows[0]
    assert final_row["Category"] == "Pitch Competition"  # untouched: was non-blank


def test_grants_runner_force_refresh_overwrites_non_blank_business_columns():
    rows = [_base_row(Category="Pitch Competition")]
    sheet = _run_once(rows, _response(category="Incubator"), force_refresh=True)
    final_row = sheet.rows[0]
    assert final_row["Category"] == "Incubator"


def test_grants_runner_composite_columns_and_bookkeeping_always_present():
    rows = [_base_row()]
    sheet = _run_once(rows, _response())
    final_row = sheet.rows[0]
    assert final_row["Eligibility"] == "Eligible — For-profit utilities startup, US-based"
    assert final_row["Requirements"] == "Pitch deck; Letter of intent"
    assert "Application link: https://example.org/apply-now" in final_row["Research_Notes"]
    assert final_row["Fit_Score"] == "55"
    assert final_row["Confidence"] == "0.85"
    assert final_row["Source_URLs"] == "https://example.org"
    assert final_row["Bot_Status"] == "Needs Review"
    assert final_row["Recommended_Action"] == "Watch"
    assert final_row["Last Checked"]


def test_grants_runner_not_eligible_routes_to_skip():
    rows = [_base_row()]
    sheet = _run_once(rows, _response(eligible="Not eligible", fit_score=95))
    final_row = sheet.rows[0]
    assert final_row["Recommended_Action"] == "Skip"


def test_grants_runner_error_after_exhausted_retries_writes_no_business_columns():
    rows = [_base_row()]
    sheet = FakeSheetsClient(rows, headers=list(BASE_HEADERS))
    llm = FakeLLMClient(["not json", "still not json", "nope"])
    worker = GrantsWorker()
    settings = Settings(_env_file=None, fetch_first=False)
    options = RunnerOptions(batch_size=5)
    run(worker, sheet, llm, settings, options)

    assert len(llm.calls) == 3
    final_row = sheet.rows[0]
    assert final_row["Bot_Status"] == "Error"
    assert final_row["Category"] == ""
    assert final_row["Fit_Score"] == ""
