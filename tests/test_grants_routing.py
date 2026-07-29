from __future__ import annotations

from datetime import date, timedelta

from workers.grants.schema import GrantsResult
from workers.grants.worker import ACTION_APPLY, ACTION_SKIP, ACTION_WATCH, GrantsWorker


def _result(**overrides) -> GrantsResult:
    base = dict(
        name="Utility Innovation Grant",
        category="Grant",
        eligible="Eligible",
        eligibility_reason=None,
        deadline=None,
        contact_email=None,
        application_link=None,
        requirements=[],
        fit_score=80,
        fit_rationale="Strong domain fit",
        recommended_action="Apply",
        draft_outline="Section 1: ...",
        confidence=0.9,
        source_urls=["https://example.org"],
        notes=None,
    )
    base.update(overrides)
    return GrantsResult(**base)


def test_not_eligible_routes_to_skip_even_with_high_fit_score():
    worker = GrantsWorker()
    result = _result(eligible="Not eligible", fit_score=95)
    assert worker.route(result) == ACTION_SKIP


def test_unclear_eligibility_routes_to_skip():
    worker = GrantsWorker()
    result = _result(eligible="Unclear", fit_score=95)
    assert worker.route(result) == ACTION_SKIP


def test_past_deadline_routes_to_skip_even_when_eligible_and_high_fit():
    worker = GrantsWorker()
    past = (date.today() - timedelta(days=10)).strftime("%Y-%m-%d")
    result = _result(eligible="Eligible", fit_score=95, deadline=past)
    assert worker.route(result) == ACTION_SKIP


def test_rolling_deadline_counts_as_feasible():
    worker = GrantsWorker()
    result = _result(eligible="Eligible", fit_score=95, deadline="Rolling")
    assert worker.route(result) == ACTION_APPLY


def test_high_fit_score_and_feasible_deadline_routes_to_apply():
    worker = GrantsWorker()
    future = (date.today() + timedelta(days=30)).strftime("%Y-%m-%d")
    result = _result(eligible="Eligible", fit_score=70, deadline=future)
    assert worker.route(result) == ACTION_APPLY


def test_low_fit_score_routes_to_watch():
    worker = GrantsWorker()
    result = _result(eligible="Eligible", fit_score=69, deadline=None)
    assert worker.route(result) == ACTION_WATCH


def test_unparseable_deadline_treated_as_feasible():
    worker = GrantsWorker()
    result = _result(eligible="Eligible", fit_score=80, deadline="Sometime next quarter")
    assert worker.route(result) == ACTION_APPLY
