"""Publishing is the one step that makes sheet data public, so the field
allowlist, the deadline normalization, and the no-duplicates guarantee are
tested directly.
"""

from __future__ import annotations

from datetime import date

import pytest

from framework.worker_base import SENTINEL_VALUE
from tests.fixtures.investors_sheet import investors_row
from wix.client import BulkItemResult, WixClient, WixError
from wix.deadline import compute_deadline_fields
from wix.publish import (
    ALLOWED_WIX_KEYS,
    FORBIDDEN_FIELDS,
    SOURCE_FIELDS,
    PublishAllowlistError,
    build_item,
    publish_rows,
    select_publishable,
    slugify,
)


class FakeWixClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.saved: dict[str, dict[str, object]] = {}
        self.calls: list[list[str]] = []
        self._fail = fail

    def save_items(self, items: dict[str, dict[str, object]]) -> list[BulkItemResult]:
        ids = list(items)
        self.calls.append(ids)
        if self._fail:
            raise WixError("simulated outage")
        results = []
        for item_id, data in items.items():
            self.saved[item_id] = data
            results.append(BulkItemResult(item_id=item_id, success=True))
        return results


def _researched_row(**overrides) -> dict[str, str]:
    row = investors_row(
        **{
            "Deadline Formula": "CURRENT",
            "Website": "https://acme.vc",
            "Industry Focus": "Climate",
            "Stage": "Pre Seed",
            "Email": "partner@acme.vc",
            "Phone": "+1-555-0100",
            "City": "Austin",
            "State/Country": "Texas",
            "Deadline": "2099-01-01",
            "Application Link": "https://acme.vc/apply",
            "Deadline Status": "Open",
            "Contact Date": "2026-01-05",
            "Status": "Emailed",
        }
    )
    row["Draft_Body"] = "Hi there"
    row["Draft_Subject"] = "Intro"
    row.update(overrides)
    return row


# -- the allowlist -------------------------------------------------------


def test_source_and_forbidden_sets_never_overlap():
    assert not (set(SOURCE_FIELDS) & FORBIDDEN_FIELDS)


def test_email_and_phone_keys_are_not_on_the_output_allowlist():
    assert "email" not in ALLOWED_WIX_KEYS
    assert "phone" not in ALLOWED_WIX_KEYS


def test_personal_and_outreach_fields_are_never_published():
    item = build_item(_researched_row())
    for value in ("partner@acme.vc", "+1-555-0100", "2026-01-05", "Emailed", "Hi there", "Intro"):
        assert value not in item.values()


def test_only_allowlisted_keys_are_published():
    item = build_item(_researched_row())
    assert set(item) <= ALLOWED_WIX_KEYS
    assert item["name"] == "Acme Ventures"
    assert item["industryFocus"] == "Climate"
    assert item["stateCountry"] == "Texas"


def test_build_item_rejects_a_key_not_on_the_allowlist(monkeypatch):
    # If a future edit adds a field build_item emits without also adding it
    # to ALLOWED_WIX_KEYS, the guard must fire rather than silently publish it.
    monkeypatch.setattr("wix.publish.ALLOWED_WIX_KEYS", frozenset({"website"}))
    with pytest.raises(PublishAllowlistError, match="name"):
        build_item(_researched_row())


def test_sentinels_and_blanks_are_not_published_as_a_literal_value():
    item = build_item(_researched_row(**{"Application Link": SENTINEL_VALUE, "City": ""}))
    assert "applicationLink" not in item
    assert "city" not in item
    assert item["location"] == "Texas"  # falls back to just the region


# -- deadline normalization -----------------------------------------------


def test_rolling_text_is_recognized_regardless_of_case():
    fields = compute_deadline_fields("rolling", None)
    assert fields.is_rolling is True
    assert fields.status == "ROLLING"
    assert fields.label == "Rolling"
    assert fields.sort_iso == "2099-12-31"
    assert fields.days_left is None


def test_deadline_status_column_can_signal_rolling_on_its_own():
    fields = compute_deadline_fields("N/A", "ROLLING")
    assert fields.is_rolling is True


def test_future_date_is_open_or_closing_soon():
    today = date(2026, 1, 1)
    far = compute_deadline_fields("2026-06-01", "Open", today=today)
    assert far.status == "OPEN"
    assert far.date_iso == "2026-06-01"
    assert far.days_left == 151

    soon = compute_deadline_fields("2026-01-10", "Open", today=today)
    assert soon.status == "CLOSING SOON"
    assert soon.days_left == 9


def test_past_date_is_marked_passed_and_sorts_to_the_far_past():
    today = date(2026, 1, 1)
    fields = compute_deadline_fields("2025-01-01", "Open", today=today)
    assert fields.status == "PASSED"
    assert fields.sort_iso == "1900-01-01"


def test_blank_deadline_is_unknown_not_an_error():
    fields = compute_deadline_fields("", "")
    assert fields.status == "UNKNOWN"
    assert fields.label == "—"
    assert fields.date_iso is None
    assert fields.sort_iso == "2099-12-31"


def test_build_item_exposes_all_deadline_fields():
    item = build_item(_researched_row(Deadline="Rolling", **{"Deadline Status": "Rolling"}))
    assert item["isRolling"] is True
    assert item["deadlineStatus"] == "ROLLING"
    assert item["deadlineLabel"] == "Rolling"
    assert item["deadlineSort"] == "2099-12-31"


# -- row selection -------------------------------------------------------


def test_only_current_rows_publish():
    rows = [
        _researched_row(),
        _researched_row(**{"Deadline Formula": "PULL VC DATA"}),
        _researched_row(**{"Deadline Formula": "STALE"}),
    ]
    assert len(select_publishable(rows, "Deadline Formula")) == 1


def test_rows_without_a_name_are_skipped():
    rows = [_researched_row(Name="")]
    assert select_publishable(rows, "Deadline Formula") == []


# -- idempotency ---------------------------------------------------------


def test_slug_is_stable_and_normalized():
    assert slugify("Acme Ventures") == "acme-ventures"
    assert slugify("  Acme   Ventures  ") == slugify("Acme Ventures")
    assert slugify("Ünïcode & Co.") == "unicode-co"


def test_publishing_twice_creates_no_duplicates():
    rows = [_researched_row()]
    client = FakeWixClient()

    first = publish_rows(rows, client, queue_column="Deadline Formula")
    second = publish_rows(rows, client, queue_column="Deadline Formula")

    assert first.published == 1
    assert second.published == 1
    assert client.calls == [["acme-ventures"], ["acme-ventures"]]
    assert len(client.saved) == 1


def test_publish_uses_one_bulk_call_for_many_rows():
    rows = [_researched_row(Name=f"Firm {i}") for i in range(5)]
    client = FakeWixClient()
    summary = publish_rows(rows, client, queue_column="Deadline Formula")
    assert summary.published == 5
    assert len(client.calls) == 1
    assert len(client.calls[0]) == 5


def test_dry_run_publishes_nothing():
    client = FakeWixClient()
    summary = publish_rows(
        [_researched_row()], client, queue_column="Deadline Formula", dry_run=True
    )
    assert summary.published == 1
    assert client.calls == []


def test_a_failed_bulk_call_is_reported_as_errors_not_raised():
    client = FakeWixClient(fail=True)
    summary = publish_rows([_researched_row()], client, queue_column="Deadline Formula")
    assert summary.errors == 1
    assert summary.published == 0


# -- client construction -------------------------------------------------


def test_client_requires_all_three_credentials():
    with pytest.raises(WixError, match="WIX_API_KEY"):
        WixClient(api_key="", site_id="site", collection_id="coll")
