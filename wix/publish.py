"""Publish researched firm rows to a Wix CMS collection.

A separate step from research, run only via `main.py --publish`. Three
invariants make it safe to re-run and safe to expose publicly:

- Only firm-level, non-personal data leaves the sheet, and it leaves under
  Wix field keys (camelCase identifiers) chosen deliberately here — never
  the raw sheet header text, which Wix's field-key rules don't accept
  anyway (e.g. "State/Country" isn't a valid key). _DIRECT_FIELD_MAP is an
  allowlist, not a denylist: a new sheet column is excluded by default and
  has to be mapped here on purpose. ALLOWED_WIX_KEYS is the corresponding
  allowlist on the output side, checked on every build_item() call.
- The raw `Deadline` cell (a free-text date, "Rolling", or blank) is never
  published as-is — it's expanded into label/date/sort/rolling/days-left
  fields by wix/deadline.py so the Wix site can actually sort and expire
  deadlines instead of treating them as opaque text.
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
from wix.deadline import compute_deadline_fields

logger = get_logger("utilyze.wix")

# Sheet header -> Wix field key, for values copied through unchanged.
# Deliberately excludes Email, Phone, Contact Date, Status, and every
# Draft_* column — none of those are ever read by this module at all.
_DIRECT_FIELD_MAP: dict[str, str] = {
    "Name": "name",
    "Website": "website",
    "Industry Focus": "industryFocus",
    "Stage": "stage",
    "City": "city",
    "State/Country": "stateCountry",
    "Application Link": "applicationLink",
}

# Sheet headers this module ever reads. Kept distinct from the Wix output
# keys below so the allowlist is legible from both ends.
SOURCE_FIELDS: tuple[str, ...] = (*_DIRECT_FIELD_MAP, "Deadline", "Deadline Status")

# Never read, spelled out so the boundary reads as intent rather than as
# the accidental complement of SOURCE_FIELDS.
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

# Every key build_item() is allowed to emit: the direct fields above, plus
# the derived deadline/search/sort fields. Nothing else may ever reach a
# Wix payload — checked at the end of every build_item() call.
ALLOWED_WIX_KEYS: frozenset[str] = frozenset(
    {
        *_DIRECT_FIELD_MAP.values(),
        "deadlineLabel",
        "deadlineDate",
        "deadlineSort",
        "isRolling",
        "daysLeft",
        "deadlineStatus",
        "location",
        "searchText",
        "nameSort",
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


def build_item(row: dict[str, str]) -> dict[str, Any]:
    """Project a sheet row onto the Wix payload shape. Only ever reads
    SOURCE_FIELDS and only ever emits ALLOWED_WIX_KEYS — Email, Phone,
    Contact Date, Status, and every Draft_* column are never referenced,
    let alone published."""
    item: dict[str, Any] = {}

    for header, key in _DIRECT_FIELD_MAP.items():
        value = (row.get(header) or "").strip()
        if not is_effectively_blank(value):
            item[key] = value

    deadline = compute_deadline_fields(row.get("Deadline"), row.get("Deadline Status"))
    item["deadlineLabel"] = deadline.label
    item["deadlineDate"] = deadline.date_iso
    item["deadlineSort"] = deadline.sort_iso
    item["isRolling"] = deadline.is_rolling
    item["daysLeft"] = deadline.days_left
    item["deadlineStatus"] = deadline.status

    city = (row.get("City") or "").strip()
    region = (row.get("State/Country") or "").strip()
    location_parts = [p for p in (city, region) if p and not is_effectively_blank(p)]
    if location_parts:
        item["location"] = ", ".join(location_parts)

    name = (row.get("Name") or "").strip()
    item["nameSort"] = name.casefold()
    search_parts = [name, row.get("Industry Focus"), row.get("Stage"), city, region]
    item["searchText"] = " ".join(
        p.strip() for p in search_parts if p and not is_effectively_blank(p)
    ).casefold()

    leaked = set(item) - ALLOWED_WIX_KEYS
    if leaked:
        raise PublishAllowlistError(f"Refusing to publish disallowed field(s): {sorted(leaked)}")
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
    items: dict[str, dict[str, Any]] = {}

    for row in select_publishable(rows, queue_column):
        name = row["Name"].strip()
        item_id = slugify(name)
        if not item_id:
            summary.skipped += 1
            continue
        items[item_id] = build_item(row)

    if dry_run:
        for item_id, item in items.items():
            log_event(logger, "dry_run_publish", item_id=item_id, fields=sorted(item))
        summary.published = len(items)
        summary.item_ids = list(items)
        return summary

    try:
        results = client.save_items(items)
    except WixError as exc:
        summary.errors = len(items)
        summary.item_ids = list(items)
        log_event(logger, "publish_failed", error=str(exc)[:300], level="ERROR")
        return summary

    for result in results:
        summary.item_ids.append(result.item_id)
        if result.success:
            summary.published += 1
        else:
            summary.errors += 1
            log_event(
                logger,
                "publish_item_failed",
                item_id=result.item_id,
                error=(result.error or "")[:300],
                level="ERROR",
            )

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
