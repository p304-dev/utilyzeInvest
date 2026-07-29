from __future__ import annotations

import pytest

from sheets.client import SheetsClient


def test_missing_credentials_file_raises_clear_error(tmp_path):
    missing_path = tmp_path / "does-not-exist.json"
    with pytest.raises(RuntimeError, match="not found"):
        SheetsClient.from_service_account(
            credentials_path=str(missing_path),
            spreadsheet_id="some-id",
            sheet_tab="Investors",
        )


def test_empty_credentials_file_raises_clear_error(tmp_path):
    empty_path = tmp_path / "service-account.json"
    empty_path.write_text("")
    with pytest.raises(RuntimeError, match="empty"):
        SheetsClient.from_service_account(
            credentials_path=str(empty_path),
            spreadsheet_id="some-id",
            sheet_tab="Investors",
        )


def test_missing_sheet_id_raises_clear_error(tmp_path):
    creds_path = tmp_path / "service-account.json"
    creds_path.write_text("{}")  # non-empty; sheet_id check happens first only if blank
    with pytest.raises(RuntimeError, match="SHEET_ID"):
        SheetsClient.from_service_account(
            credentials_path=str(creds_path),
            spreadsheet_id="",
            sheet_tab="Investors",
        )
