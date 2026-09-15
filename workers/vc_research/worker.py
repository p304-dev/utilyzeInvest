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

from framework.worker_base import SENTINEL_VALUE, Worker, format_value, is_effectively_blank
from workers.vc_research.schema import VCRecheckResult, VCResearchResult

_PROMPT_PATH = Path(__file__).parent / "prompt.md"
_RECHECK_PROMPT_PATH = Path(__file__).parent / "recheck_prompt.md"

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
        "category": "Category",
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
    }

    # These reflect the worker's own assessment, not ground-truth business
    # data, so they're refreshed on every processed row.
    always_overwrite_fields = frozenset({"confidence", "source_urls", "notes"})

    sentinel_columns = _SCANNED_COLUMNS

    human_owned_columns = ["Status", "Contact Date"]
    formula_columns = ["Deadline Formula", "Deadline Status"]

    queue_column = "Deadline Formula"
    queue_work_values = frozenset({"PULL VC DATA", "STALE"})

    timestamp_column = "Last Checked"
    # These already exist on the tab and are load-bearing for the queue
    # formula — creating a second copy would leave the formula's own cell
    # blank and re-queue every row forever.
    extra_required_columns = ["Last Checked", "Deadline Formula", "Deadline Status"]

    route_column = "Recommended_Channel"

    # A STALE row (already researched, just due a 90-day refresh) only
    # re-verifies deadline/application-link accuracy — see recheck_prompt.md
    # and compute_recheck_updates() below. A brand-new row (PULL VC DATA)
    # always gets the full research path regardless of this.
    recheck_schema = VCRecheckResult

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
            template.replace("{{Name}}", row.get("Name", ""))
            .replace("{{Website}}", website)
            .replace("{{KNOWN_FIELDS}}", self._known_fields_block(row))
        )

    def _known_fields_block(self, row: dict[str, str]) -> str:
        """Whatever a human (or an earlier pass) already put in this row,
        so the model doesn't burn search effort re-deriving it. Website is
        excluded — it's already surfaced separately above as an identity
        hint, not as a field to skip researching.

        A real value and the sentinel value both mean "don't research
        this" — they just mean different things to echo back: a real value
        gets copied as-is, while a sentinel means an earlier pass already
        confirmed nothing exists, so the model should return null for it
        rather than trying again.
        """
        known = []
        confirmed_absent = []
        for field_name, header in self.column_map.items():
            if field_name in self.always_overwrite_fields or header == "Website":
                continue
            stripped = (row.get(header) or "").strip()
            if not stripped:
                continue  # truly blank — needs research
            if stripped == SENTINEL_VALUE:
                confirmed_absent.append(f"- {header}")
            else:
                known.append(f"- {header}: {stripped}")

        if not known and not confirmed_absent:
            return "(Nothing pre-filled for this row — research every field below.)"

        sections = []
        if known:
            sections.append(
                "Already confirmed — return these exact values unchanged, no need to "
                "re-verify or source them:\n" + "\n".join(known)
            )
        if confirmed_absent:
            sections.append(
                "Already checked in an earlier pass and confirmed NOT publicly "
                "available — return null for these, do not search for them again:\n"
                + "\n".join(confirmed_absent)
            )
        return "\n\n".join(sections)

    def build_recheck_prompt(self, row: dict[str, str], settings: Any) -> str:
        template = _RECHECK_PROMPT_PATH.read_text(encoding="utf-8")
        return (
            template.replace("{{Name}}", row.get("Name", ""))
            .replace("{{Website}}", row.get("Website", ""))
            .replace("{{Deadline}}", row.get("Deadline", ""))
            .replace("{{Application Link}}", row.get("Application Link", ""))
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
        # industry_focus/category excluded: both always default to a real
        # value (Generalist/Investor) rather than null, so neither is
        # evidence the cheap extraction actually found anything.
        researched = [
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
        # industry_focus/category are excluded: both always default to a
        # real value (Generalist/Investor) rather than null, so neither is
        # evidence anything was actually found.
        partial = any(
            value and value.strip()
            for value in (
                result.website,
                result.stage,
                result.city,
                result.state_or_country,
            )
        )
        return CHANNEL_MANUAL_REVIEW if partial else CHANNEL_RESEARCH_FAILED

    def compute_recheck_updates(
        self, result: BaseModel, row: dict[str, str], force_refresh: bool
    ) -> dict[str, str]:
        assert isinstance(result, VCRecheckResult)
        updates: dict[str, str] = {}

        # Only overwrite when the recheck actually found a value. A null
        # here means "couldn't confirm," not "confirmed absent" — unlike a
        # first-pass research call, an inconclusive recheck must never
        # blank out data a full research pass already verified.
        if result.deadline and result.deadline.strip():
            updates["Deadline"] = result.deadline.strip()
        if result.application_link and result.application_link.strip():
            updates["Application Link"] = result.application_link.strip()

        updates["Confidence"] = format_value(result.confidence)
        updates["Source_URLs"] = format_value(result.source_urls)
        if result.notes:
            updates["Research_Notes"] = f"[Recheck] {result.notes.strip()}"

        return updates
