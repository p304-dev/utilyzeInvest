"""Publishing is the one step that makes sheet data public, so the field
allowlist and the no-duplicates guarantee are tested directly.
"""

from __future__ import annotations

import pytest

from tests.fixtures.investors_sheet import investors_row
from wix.client import WixClient, WixError
from wix.publish import (
    FORBIDDEN_FIELDS,
    PUBLISH_FIELDS,
    PublishAllowlistError,
    build_item,
    publish_rows,
    select_publishable,
    slugify,
)


class FakeWixClient:
    def __init__(self) -> None:
        self.saved: dict[str, dict[str, str]] = {}
        self.calls: list[str] = []

    def save_item(self, item_id: str, data: dict[str, str]) -> dict[str, object]:
        self.calls.append(item_id)
        # Mirrors Save Data Item's upsert-on-id behavior.
        action = "UPDATED" if item_id in self.saved else "INSERTED"
        self.saved[item_id] = data
        return {"action": action, "dataItem": {"id": item_id, "data": data}}


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


def test_allowlist_and_forbidden_sets_never_overlap():
    assert not (set(PUBLISH_FIELDS) & FORBIDDEN_FIELDS)


def test_personal_and_outreach_fields_are_never_published():
    item = build_item(_researched_row())
    for forbidden in ("Email", "Phone", "Contact Date", "Status", "Draft_Body", "Draft_Subject"):
        assert forbidden not in item


def test_only_allowlisted_fields_are_published():
    item = build_item(_researched_row())
    assert set(item) <= set(PUBLISH_FIELDS)
    assert item["Name"] == "Acme Ventures"
    assert item["Industry Focus"] == "Climate"


def test_build_item_rejects_a_disallowed_field(monkeypatch):
    # If someone adds a contact field to the allowlist, the guard fires.
    monkeypatch.setattr("wix.publish.PUBLISH_FIELDS", (*PUBLISH_FIELDS, "Email"))
    with pytest.raises(PublishAllowlistError, match="Email"):
        build_item(_researched_row())


def test_sentinels_are_not_published_as_literal_none():
    item = build_item(_researched_row(**{"Application Link": "None", "City": ""}))
    assert "Application Link" not in item
    assert "City" not in item


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
    assert client.calls == ["acme-ventures", "acme-ventures"]
    assert len(client.saved) == 1


def test_dry_run_publishes_nothing():
    client = FakeWixClient()
    summary = publish_rows(
        [_researched_row()], client, queue_column="Deadline Formula", dry_run=True
    )
    assert summary.published == 1
    assert client.calls == []


# -- client construction -------------------------------------------------


def test_client_requires_all_three_credentials():
    with pytest.raises(WixError, match="WIX_API_KEY"):
        WixClient(api_key="", site_id="site", collection_id="coll")
