"""The vc_research worker: input columns, schema, column map, prompt
building, and channel-routing logic for the Investors tab.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from framework.worker_base import Worker
from workers.vc_research.schema import VCResearchResult

_PROMPT_PATH = Path(__file__).parent / "prompt.md"

CHANNEL_APPLY = "Apply"
CHANNEL_EMAIL = "Email"
CHANNEL_CONTACT_BOX = "Contact Box"
CHANNEL_LINKEDIN = "LinkedIn"


class VCResearchWorker(Worker):
    name = "vc_research"
    sheet_tab = "Investors"
    input_columns = ["Investor Name", "Website"]
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
        "contact_first_name": "First",
        "contact_last_name": "Last",
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

    def build_prompt(self, row: dict[str, str], settings: Any) -> str:
        template = _PROMPT_PATH.read_text(encoding="utf-8")
        return (
            template.replace("{{UTILYZE_CONTEXT}}", settings.utilyze_context)
            .replace("{{Investor Name}}", row.get("Investor Name", ""))
            .replace("{{Website}}", row.get("Website", ""))
        )

    def route(self, result: BaseModel) -> str:
        assert isinstance(result, VCResearchResult)
        if result.application_link:
            return CHANNEL_APPLY
        if result.email and result.contact_first_name:
            return CHANNEL_EMAIL
        if result.has_contact_form and not result.email:
            return CHANNEL_CONTACT_BOX
        return CHANNEL_LINKEDIN

    def after_research(
        self,
        row: dict[str, str],
        result: BaseModel,
        channel: str,
        settings: Any,
    ) -> dict[str, str]:
        assert isinstance(result, VCResearchResult)

        if channel == CHANNEL_EMAIL and result.email and settings.gmail_enabled:
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
