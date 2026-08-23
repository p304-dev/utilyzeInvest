"""The vc_research worker: input columns, schema, column map, prompt
building, and channel-routing logic for the Investors tab.

The queue lives in the sheet, not here: column Q (`Deadline Formula`)
reports `PULL VC DATA` / `STALE` while a row needs work, and flips itself
to `CURRENT` once columns D–O are filled and `Last Checked` is stamped.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from framework.worker_base import Worker, is_effectively_blank
from workers.vc_research.schema import VCResearchResult

_PROMPT_PATH = Path(__file__).parent / "prompt.md"

CHANNEL_APPLY = "Apply"
CHANNEL_EMAIL = "Email"
CHANNEL_CONTACT_BOX = "Contact Box"
CHANNEL_LINKEDIN = "LinkedIn"
CHANNEL_TWITTER = "Twitter"
CHANNEL_MANUAL_REVIEW = "Manual Review"
CHANNEL_RESEARCH_FAILED = "Research Failed"

# Columns D–O — exactly the range the sheet's queue formula scans with
# COUNTBLANK. Every one must end up non-blank after a row is processed.
_SCANNED_COLUMNS = frozenset(
    {
        "Website",
        "Industry Focus",
        "Stage",
        "Email",
        "Phone",
        "City",
        "State/Country",
        "Deadline",
        "Application Link",
        "LinkedIn",
        "Twitter",
        "Newsletter Yes/No",
    }
)

# How many researched fields the cheap fetch-then-extract path must land
# before we accept it and skip the (much pricier) web-search call.
_MIN_FIELDS_FOR_CHEAP_PATH = 4


class VCResearchWorker(Worker):
    name = "vc_research"
    sheet_tab = "Investors"
    input_columns = ["Name", "Website"]
    output_schema = VCResearchResult

    # pydantic field name -> sheet header. has_contact_form is deliberately
    # absent: it only feeds route(), it is never written to a column.
    column_map = {
        "website": "Website",
        "industry_focus": "Industry Focus",
        "stage": "Stage",
        "email": "Email",
        "phone": "Phone",
        "city": "City",
        "state_or_country": "State/Country",
        "deadline": "Deadline",
        "application_link": "Application Link",
        "linkedin_url": "LinkedIn",
        "twitter_url": "Twitter",
        "newsletter": "Newsletter Yes/No",
        "confidence": "Confidence",
        "source_urls": "Source_URLs",
        "notes": "Research_Notes",
        "draft_subject": "Draft_Subject",
        "draft_body": "Draft_Body",
    }

    # These reflect the worker's own assessment, not ground-truth business
    # data, so they're refreshed on every processed row.
    always_overwrite_fields = frozenset(
        {"confidence", "source_urls", "notes", "draft_subject", "draft_body"}
    )

    sentinel_columns = _SCANNED_COLUMNS

    human_owned_columns = ["Status", "Contact Date", "Category"]
    formula_columns = ["Deadline Formula", "Deadline Status"]

    queue_column = "Deadline Formula"
    queue_work_values = frozenset({"PULL VC DATA", "STALE"})

    timestamp_column = "Last Checked"
    # These already exist on the tab and are load-bearing for the queue
    # formula — creating a second copy would leave the formula's own cell
    # blank and re-queue every row forever.
    extra_required_columns = ["Last Checked", "Deadline Formula", "Deadline Status"]

    route_column = "Recommended_Channel"
    extra_columns = ["Draft_Subject", "Draft_Body", "Draft_Status", "Draft_Link"]

    def configure(self, settings: Any) -> None:
        """Let a queue-header rename be a config change, not a code change."""
        queue_column = getattr(settings, "queue_column", "")
        if queue_column and queue_column != self.queue_column:
            self.queue_column = queue_column
            if queue_column not in self.formula_columns:
                self.formula_columns = [*self.formula_columns, queue_column]

    def build_prompt(self, row: dict[str, str], settings: Any) -> str:
        template = _PROMPT_PATH.read_text(encoding="utf-8")
        website = row.get("Website", "")
        if is_effectively_blank(website):
            website = ""
        return (
            template.replace("{{UTILYZE_CONTEXT}}", settings.utilyze_context)
            .replace("{{Name}}", row.get("Name", ""))
            .replace("{{Website}}", website)
        )

    def page_hint(self, row: dict[str, str]) -> str | None:
        website = (row.get("Website") or "").strip()
        if is_effectively_blank(website):
            return None
        if not website.startswith(("http://", "https://")):
            website = f"https://{website}"
        return website

    def is_extraction_sufficient(self, result: BaseModel) -> bool:
        assert isinstance(result, VCResearchResult)
        researched = [
            result.industry_focus,
            result.stage,
            result.email,
            result.city,
            result.state_or_country,
            result.application_link,
            result.linkedin_url,
            result.twitter_url,
        ]
        found = sum(1 for value in researched if value and value.strip())
        return found >= _MIN_FIELDS_FOR_CHEAP_PATH

    def route(self, result: BaseModel) -> str:
        assert isinstance(result, VCResearchResult)
        if result.application_link:
            return CHANNEL_APPLY
        if result.email:
            return CHANNEL_EMAIL
        if result.has_contact_form:
            return CHANNEL_CONTACT_BOX
        if result.linkedin_url:
            return CHANNEL_LINKEDIN
        if result.twitter_url:
            return CHANNEL_TWITTER
        partial = any(
            value and value.strip()
            for value in (
                result.website,
                result.industry_focus,
                result.stage,
                result.city,
                result.state_or_country,
            )
        )
        return CHANNEL_MANUAL_REVIEW if partial else CHANNEL_RESEARCH_FAILED

    def after_research(
        self,
        row: dict[str, str],
        result: BaseModel,
        route_value: str,
        settings: Any,
    ) -> dict[str, str]:
        assert isinstance(result, VCResearchResult)

        if route_value == CHANNEL_EMAIL and result.email and settings.gmail_enabled:
            from gmail.client import GmailClient

            gmail_client = GmailClient(
                credentials_path=settings.google_application_credentials,
                sender=settings.gmail_sender,
            )
            draft_link = gmail_client.create_draft(
                to=result.email,
                subject=result.draft_subject,
                body=result.draft_body,
            )
            return {"Draft_Status": "Gmail Draft Created", "Draft_Link": draft_link}

        return {"Draft_Status": "Drafted"}
