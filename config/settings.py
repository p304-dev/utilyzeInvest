"""Environment-driven configuration for the worker framework.

All runtime config is loaded here, once, via pydantic-settings. Nothing
else in the codebase should call os.environ directly — this is the single
source of truth for env-derived values, so tests can construct a Settings
object explicitly instead of monkeypatching the environment.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Anthropic ---
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"
    anthropic_effort: str = "medium"
    anthropic_max_tokens: int = 4096
    web_search_max_uses: int = 5

    # --- Google Sheets ---
    google_application_credentials: str = "./service-account.json"
    sheet_id: str = ""

    # --- Runner behavior ---
    # Sized for the ~968-row backfill: ~4 biweekly runs clears it, and a
    # run stays well inside the 6-hour GitHub Actions job limit. Steady
    # state after that is a handful of rows per run.
    batch_size: int = 250
    confidence_min: float = Field(default=0.65, ge=0.0, le=1.0)

    # The header holding the sheet's queue formula. Overridable so renaming
    # the column in the sheet doesn't require a code change.
    queue_column: str = "Deadline Formula"

    # Fetch a row's known website and extract from that page before falling
    # back to a web-search-enabled call. Much cheaper per row.
    fetch_first: bool = True
    fetch_timeout_seconds: float = 15.0
    fetch_max_chars: int = 20_000

    # --- Gmail ---
    gmail_enabled: bool = False
    gmail_sender: str = "ana.valentino@utilyze.ai"

    # --- Wix CMS publish (--publish only; never part of a research run) ---
    wix_api_key: str = ""
    wix_site_id: str = ""
    wix_collection_id: str = ""

    # --- Prompt company context ---
    utilyze_context_file: str = "config/utilyze_context.md"

    log_level: str = "INFO"

    @field_validator("gmail_enabled", mode="before")
    @classmethod
    def _blank_env_means_default(cls, value: Any) -> Any:
        # An unset GitHub Actions repo/environment variable interpolates to
        # "" rather than being absent, which pydantic's bool parser rejects
        # outright. Treat blank the same as unset (falls back to False).
        if isinstance(value, str) and value.strip() == "":
            return False
        return value

    @property
    def utilyze_context(self) -> str:
        path = Path(self.utilyze_context_file)
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8").strip()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
