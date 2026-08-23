"""The fetch-then-extract path exists to cut cost, so what matters is that
it (a) skips web search when it works and (b) always falls back cleanly.
"""

from __future__ import annotations

import json

from config.settings import Settings
from framework.runner import RunnerOptions, run
from llm.fetch import html_to_text
from tests.fixtures.fake_llm import FakeLLMClient
from tests.fixtures.fake_sheets import FakeSheetsClient
from tests.fixtures.investors_sheet import INVESTORS_HEADERS, investors_row
from workers.vc_research.worker import VCResearchWorker

_RICH = {
    "investor_name": "Acme Ventures",
    "website": "https://acme.vc",
    "industry_focus": "Climate",
    "stage": "Pre Seed",
    "email": "hello@acme.vc",
    "phone": None,
    "city": "Austin",
    "state_or_country": "Texas",
    "deadline": "Rolling",
    "application_link": None,
    "linkedin_url": None,
    "twitter_url": None,
    "newsletter": None,
    "has_contact_form": False,
    "draft_subject": "Intro",
    "draft_body": "Hello",
    "confidence": 0.9,
    "source_urls": ["https://acme.vc"],
    "notes": None,
}


def _thin() -> dict:
    thin = dict(_RICH)
    thin.update(
        industry_focus=None, stage=None, email=None, city=None, state_or_country=None
    )
    return thin


def _run(monkeypatch, page_text, extract_payload, *, row=None):
    monkeypatch.setattr("llm.fetch.fetch_page_text", lambda *a, **k: page_text)
    rows = [row or investors_row(Website="https://acme.vc")]
    sheet = FakeSheetsClient(rows, headers=list(INVESTORS_HEADERS))
    llm = FakeLLMClient(
        [json.dumps(_RICH)],
        extract_responses=[json.dumps(extract_payload)] if extract_payload else [],
    )
    run(
        VCResearchWorker(),
        sheet,
        llm,
        Settings(_env_file=None, fetch_first=True),
        RunnerOptions(batch_size=5),
    )
    return llm


def test_a_rich_page_skips_the_web_search_call(monkeypatch):
    llm = _run(monkeypatch, "Acme Ventures, a climate fund in Austin.", _RICH)
    assert len(llm.extract_calls) == 1
    assert llm.calls == [], "web search should not run when extraction sufficed"


def test_a_thin_page_falls_back_to_web_search(monkeypatch):
    llm = _run(monkeypatch, "Coming soon.", _thin())
    assert len(llm.extract_calls) == 1
    assert len(llm.calls) == 1


def test_an_unfetchable_page_falls_back_to_web_search(monkeypatch):
    llm = _run(monkeypatch, None, None)
    assert llm.extract_calls == []
    assert len(llm.calls) == 1


def test_a_row_without_a_website_never_fetches(monkeypatch):
    llm = _run(monkeypatch, "unused", None, row=investors_row(Website=""))
    assert llm.extract_calls == []
    assert len(llm.calls) == 1


def test_a_sentinel_website_is_not_treated_as_a_real_url(monkeypatch):
    # "None" means researched-and-absent; fetching it would be nonsense.
    llm = _run(monkeypatch, "unused", None, row=investors_row(Website="None"))
    assert llm.extract_calls == []
    assert len(llm.calls) == 1


def test_fetch_first_disabled_goes_straight_to_web_search(monkeypatch):
    monkeypatch.setattr("llm.fetch.fetch_page_text", lambda *a, **k: "rich page")
    sheet = FakeSheetsClient(
        [investors_row(Website="https://acme.vc")], headers=list(INVESTORS_HEADERS)
    )
    llm = FakeLLMClient([json.dumps(_RICH)])
    run(
        VCResearchWorker(),
        sheet,
        llm,
        Settings(_env_file=None, fetch_first=False),
        RunnerOptions(batch_size=5),
    )
    assert llm.extract_calls == []
    assert len(llm.calls) == 1


def test_page_hint_adds_a_scheme_when_the_sheet_omits_it():
    worker = VCResearchWorker()
    assert worker.page_hint({"Website": "acme.vc"}) == "https://acme.vc"
    assert worker.page_hint({"Website": "https://acme.vc"}) == "https://acme.vc"
    assert worker.page_hint({"Website": "None"}) is None
    assert worker.page_hint({"Website": ""}) is None


def test_html_to_text_drops_markup_and_scripts():
    html = "<html><head><style>a{}</style></head><body><script>x()</script>" \
           "<h1>Acme</h1><p>Climate&nbsp;fund</p></body></html>"
    text = html_to_text(html)
    assert "Acme" in text
    assert "Climate fund" in text
    assert "x()" not in text
    assert "<" not in text
