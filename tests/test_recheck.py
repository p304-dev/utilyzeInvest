"""The cheap-recheck path exists specifically to make a STALE row cost less
than a brand-new one — so what matters here is that a STALE row takes a
genuinely different, narrower path than PULL VC DATA, that it never blanks
out data an inconclusive recheck couldn't reconfirm, and that it leaves
routing alone (it wasn't re-derived, so it shouldn't be touched).
"""

from __future__ import annotations

import json

from config.settings import Settings
from framework.worker_base import SENTINEL_VALUE
from framework.runner import RunnerOptions, run
from tests.fixtures.fake_llm import FakeLLMClient
from tests.fixtures.fake_sheets import FakeSheetsClient
from tests.fixtures.investors_sheet import INVESTORS_HEADERS, investors_row
from workers.grants.worker import GrantsWorker
from workers.vc_research.worker import VCResearchWorker


def _already_researched_row(**overrides) -> dict[str, str]:
    """A row that already went through a full research pass and is now due
    a recheck — every scanned column is filled, plus prior bookkeeping."""
    row = investors_row(
        **{
            "Deadline Formula": "STALE",
            "Website": "https://acme.vc",
            "Industry Focus": "Climate",
            "Stage": "Pre Seed",
            "Email": "partner@acme.vc",
            "Phone": SENTINEL_VALUE,
            "City": "Austin",
            "State/Country": "Texas",
            "Deadline": "Rolling",
            "Application Link": SENTINEL_VALUE,
            "LinkedIn": "https://linkedin.com/company/acme",
            "Twitter": SENTINEL_VALUE,
            "Newsletter Yes/No": "Yes",
            "Last Checked": "2026-06-01",
        }
    )
    row["Recommended_Channel"] = "Email"
    row.update(overrides)
    return row


def _recheck_response(**overrides) -> str:
    base = {
        "investor_name": "Acme Ventures",
        "deadline": "Rolling",
        "application_link": None,
        "confidence": 0.9,
        "source_urls": ["https://acme.vc"],
        "notes": None,
    }
    base.update(overrides)
    return json.dumps(base)


def _settings() -> Settings:
    return Settings(_env_file=None, fetch_first=True)  # on, to prove recheck skips it anyway


class _RecheckAwareFakeLLM(FakeLLMClient):
    """Distinguishes recheck calls from full-research calls by routing
    through the same `research()` entrypoint the runner already uses for
    both — the two paths are told apart by which canned queue was primed."""

    def __init__(self, research_responses=None, recheck_responses=None):
        super().__init__(research_responses or [])
        self._recheck_responses = list(recheck_responses or [])
        self.recheck_calls: list[str] = []

    def research(self, prompt: str) -> str:
        # A recheck prompt is short and worker-specific; detect it by
        # content so this fake doesn't need runner-internal knowledge.
        if "re-verifying two fields" in prompt or self._recheck_responses:
            self.recheck_calls.append(prompt)
            if not self._recheck_responses:
                raise AssertionError("ran out of canned recheck responses")
            return self._recheck_responses.pop(0)
        self.calls.append(prompt)
        if not self._responses:
            raise AssertionError("ran out of canned research responses")
        return self._responses.pop(0)


def _run_worker(rows, worker, llm, force_refresh=False):
    sheet = FakeSheetsClient(rows, headers=list(INVESTORS_HEADERS))
    options = RunnerOptions(batch_size=10, force_refresh=force_refresh)
    run(worker, sheet, llm, _settings(), options)
    return sheet


def test_stale_row_is_flagged_as_a_recheck_not_a_fresh_row():
    worker = VCResearchWorker()
    stale_row = _already_researched_row()
    fresh_row = investors_row(**{"Deadline Formula": "PULL VC DATA"})
    assert worker.wants_recheck(stale_row) is True
    assert worker.wants_recheck(fresh_row) is False


def test_grants_worker_never_rechecks():
    # No queue formula on that tab at all, so recheck can never apply.
    worker = GrantsWorker()
    assert worker.wants_recheck({"Deadline Formula": "STALE"}) is False


def test_stale_row_uses_the_recheck_prompt_and_schema_not_full_research():
    llm = _RecheckAwareFakeLLM(recheck_responses=[_recheck_response()])
    sheet = _run_worker([_already_researched_row()], VCResearchWorker(), llm)

    assert len(llm.recheck_calls) == 1
    assert llm.calls == []  # never touched the full research path
    assert not sheet.write_calls[0][1].get("Website")  # not re-derived at all


def test_recheck_never_calls_the_cheap_fetch_first_path():
    llm = _RecheckAwareFakeLLM(recheck_responses=[_recheck_response()])
    _run_worker([_already_researched_row()], VCResearchWorker(), llm)
    assert llm.extract_calls == []


def test_recheck_leaves_routing_untouched():
    llm = _RecheckAwareFakeLLM(recheck_responses=[_recheck_response()])
    sheet = _run_worker([_already_researched_row()], VCResearchWorker(), llm)

    final = sheet.rows[0]
    assert final["Recommended_Channel"] == "Email"  # unchanged, not recomputed


def test_recheck_updates_deadline_when_it_changed():
    llm = _RecheckAwareFakeLLM(recheck_responses=[_recheck_response(deadline="2026-12-01")])
    sheet = _run_worker([_already_researched_row()], VCResearchWorker(), llm)
    assert sheet.rows[0]["Deadline"] == "2026-12-01"


def test_recheck_inconclusive_result_does_not_blank_the_existing_deadline():
    # A null here means "couldn't confirm" — not "confirmed gone." Blanking
    # a value a full research pass already found would be a real regression.
    llm = _RecheckAwareFakeLLM(recheck_responses=[_recheck_response(deadline=None)])
    sheet = _run_worker([_already_researched_row()], VCResearchWorker(), llm)
    assert sheet.rows[0]["Deadline"] == "Rolling"  # untouched, not overwritten with None


def test_recheck_stamps_last_checked_and_flips_the_queue():
    llm = _RecheckAwareFakeLLM(recheck_responses=[_recheck_response()])
    sheet = _run_worker([_already_researched_row()], VCResearchWorker(), llm)
    final = sheet.rows[0]
    assert final["Last Checked"] != "2026-06-01"
    assert final["Bot_Status"] == "Needs Review"


def test_recheck_low_confidence_still_flags_the_row():
    llm = _RecheckAwareFakeLLM(recheck_responses=[_recheck_response(confidence=0.2)])
    sheet = _run_worker([_already_researched_row()], VCResearchWorker(), llm)
    assert "LOW CONFIDENCE" in sheet.rows[0]["Research_Notes"]


def test_pull_vc_data_row_still_gets_full_research_unaffected():
    from tests.test_vc_queue_and_sentinels import _response

    llm = _RecheckAwareFakeLLM(research_responses=[_response()])
    fresh_row = investors_row(**{"Deadline Formula": "PULL VC DATA"})
    sheet = _run_worker([fresh_row], VCResearchWorker(), llm)

    assert llm.recheck_calls == []
    assert len(llm.calls) == 1
    assert sheet.rows[0]["Website"] == "https://acme.vc"
