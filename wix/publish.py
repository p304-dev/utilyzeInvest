"""Publish researched firm rows to a Wix CMS collection.

A separate step from research, run only via `main.py --publish`. Two
invariants make it safe to re-run and safe to expose publicly:

- Only firm-level, non-personal fields leave the sheet. PUBLISH_FIELDS is
  an allowlist, not a denylist: a new sheet column is excluded by default
  and has to be added here deliberately.
- The Wix item ID is a deterministic slug of the firm name, and Save Data
  Item upserts on it, so publishing twice updates rather than duplicates.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from framework.logging import get_logger, log_event
from framework.worker_base import is_effectively_blank
from wix.client import WixClient, WixError

logger = get_logger("utilyze.wix")

# The ONLY sheet headers that may ever be published. Firm-level facts a
# visitor could look up themselves — never a contact route, never a draft,
# never anything a human wrote about our own outreach progress.
PUBLISH_FIELDS: tuple[str, ...] = (
    "Name",
    "Website",
    "Industry Focus",
    "Stage",
    "City",
    "State/Country",
    "Application Link",
    "Deadline Status",
)

# Never publishable, spelled out so the guard reads as intent rather than
# as the accidental complement of the allowlist.
FORBIDDEN_FIELDS: frozenset[str] = frozenset(
    {
        "Email",
        "Phone",
        "Contact Date",
        "Status",
        "Draft_Subject",
        "Draft_Body",
        "Draft_Status",
        "Draft_Link",
    }
)

# Rows are published once fully researched and fresh — which is exactly
# what the sheet's own queue formula already computes.
PUBLISHABLE_QUEUE_VALUES: frozenset[str] = frozenset({"CURRENT"})

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


class PublishAllowlistError(Exception):
    """A disallowed field reached the publish payload."""


@dataclass
class PublishSummary:
    published: int = 0
    skipped: int = 0
    errors: int = 0
    item_ids: list[str] = field(default_factory=list)


def slugify(name: str) -> str:
    """Stable, normalized item ID. Same firm name always yields the same
    slug, so Save Data Item updates instead of creating a duplicate."""
    normalized = unicodedata.normalize("NFKD", name)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = _SLUG_STRIP.sub("-", ascii_only).strip("-")
    return slug


def build_item(row: dict[str, str]) -> dict[str, str]:
    """Project a sheet row onto the allowlisted publish fields, dropping
    blanks and sentinels so the CMS never shows a literal 'None'."""
    item: dict[str, str] = {}
    for header in PUBLISH_FIELDS:
        value = (row.get(header) or "").strip()
        if is_effectively_blank(value):
            continue
        item[header] = value

    leaked = set(item) & FORBIDDEN_FIELDS
    if leaked:
        raise PublishAllowlistError(f"Refusing to publish disallowed field(s): {sorted(leaked)}")
    disallowed = set(item) - set(PUBLISH_FIELDS)
    if disallowed:
        raise PublishAllowlistError(f"Field(s) not on the allowlist: {sorted(disallowed)}")
    return item


def select_publishable(rows: list[dict[str, str]], queue_column: str) -> list[dict[str, str]]:
    """Rows the sheet reports as fully researched and fresh."""
    wanted = {v.casefold() for v in PUBLISHABLE_QUEUE_VALUES}
    selected = []
    for row in rows:
        if (row.get(queue_column) or "").strip().casefold() not in wanted:
            continue
        if not (row.get("Name") or "").strip():
            continue
        selected.append(row)
    return selected


def publish_rows(
    rows: list[dict[str, str]],
    client: WixClient,
    *,
    queue_column: str,
    dry_run: bool = False,
) -> PublishSummary:
    summary = PublishSummary()

    for row in select_publishable(rows, queue_column):
        name = row["Name"].strip()
        item_id = slugify(name)
        if not item_id:
            summary.skipped += 1
            continue

        item = build_item(row)
        summary.item_ids.append(item_id)

        if dry_run:
            log_event(logger, "dry_run_publish", item_id=item_id, fields=sorted(item))
            summary.published += 1
            continue

        try:
            client.save_item(item_id, item)
        except WixError as exc:
            summary.errors += 1
            log_event(logger, "publish_failed", item_id=item_id, error=str(exc)[:300], level="ERROR")
            continue
        summary.published += 1

    log_event(
        logger,
        "publish_summary",
        published=summary.published,
        skipped=summary.skipped,
        errors=summary.errors,
    )
    return summary


def build_client(settings: Any) -> WixClient:
    return WixClient(
        api_key=settings.wix_api_key,
        site_id=settings.wix_site_id,
        collection_id=settings.wix_collection_id,
    )
