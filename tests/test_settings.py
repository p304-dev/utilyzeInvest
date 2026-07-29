from __future__ import annotations

from config.settings import Settings


def test_gmail_enabled_blank_env_value_falls_back_to_false(monkeypatch):
    # Reproduces the GitHub Actions failure mode: an unset repo/environment
    # variable interpolates to "" rather than being absent from the
    # environment, which pydantic's bool parser otherwise rejects outright.
    monkeypatch.setenv("GMAIL_ENABLED", "")
    settings = Settings(_env_file=None)
    assert settings.gmail_enabled is False


def test_gmail_enabled_true_string_parses_normally(monkeypatch):
    monkeypatch.setenv("GMAIL_ENABLED", "true")
    settings = Settings(_env_file=None)
    assert settings.gmail_enabled is True


def test_gmail_enabled_defaults_false_when_unset(monkeypatch):
    monkeypatch.delenv("GMAIL_ENABLED", raising=False)
    settings = Settings(_env_file=None)
    assert settings.gmail_enabled is False
