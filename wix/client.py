"""Wix CMS transport.

Shapes confirmed against the Wix Data v2 reference:

Save Data Item (single, upsert on `dataItem.id`):
    POST https://www.wixapis.com/wix-data/v2/items/save
    {"dataCollectionId": "...", "dataItem": {"id": "...", "data": {...}}}

Bulk Save Data Items (up to 1000 per call, each upserts on `dataItems[].id`):
    POST https://www.wixapis.com/data/v2/bulk/items/save
    {"dataCollectionId": "...", "dataItems": [{"id": "...", "data": {...}}, ...]}
    -> {"results": [{"action", "itemMetadata": {"id", "originalIndex", "success", "error"}}],
        "bulkActionMetadata": {"totalSuccesses", "totalFailures"}}
    A 200 here means the call was accepted — check each result's
    itemMetadata.success individually; one bad item doesn't fail the batch.

Bulk Remove Data Items (up to 1000 IDs per call):
    POST https://www.wixapis.com/data/v2/bulk/items/remove
    {"dataCollectionId": "...", "dataItemIds": ["...", ...]}
    -> same {"results": [...], "bulkActionMetadata": {...}} shape as save.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

SAVE_ITEM_URL = "https://www.wixapis.com/wix-data/v2/items/save"
BULK_SAVE_URL = "https://www.wixapis.com/data/v2/bulk/items/save"
BULK_REMOVE_URL = "https://www.wixapis.com/data/v2/bulk/items/remove"

# Wix's own documented cap on how many items one bulk call can carry.
BULK_MAX_ITEMS = 1000


class WixError(Exception):
    """A Wix Data call failed outright (transport error or non-2xx)."""


@dataclass
class BulkItemResult:
    item_id: str
    success: bool
    error: str | None = None


def _chunk(items: list[Any], size: int) -> list[list[Any]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


class WixClient:
    def __init__(
        self,
        *,
        api_key: str,
        site_id: str,
        collection_id: str,
        timeout: float = 30.0,
    ) -> None:
        if not api_key or not site_id or not collection_id:
            raise WixError(
                "Wix publishing needs WIX_API_KEY, WIX_SITE_ID and "
                "WIX_COLLECTION_ID to all be set."
            )
        self._api_key = api_key
        self._site_id = site_id
        self._collection_id = collection_id
        self._timeout = timeout

    @property
    def collection_id(self) -> str:
        return self._collection_id

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": self._api_key,
            "wix-site-id": self._site_id,
            "Content-Type": "application/json",
        }

    def _post(self, url: str, payload: dict[str, Any], *, what: str) -> dict[str, Any]:
        try:
            response = requests.post(
                url, json=payload, headers=self._headers(), timeout=self._timeout
            )
        except requests.RequestException as exc:
            raise WixError(f"{what} request failed: {exc}") from exc

        if response.status_code >= 400:
            raise WixError(f"{what} returned {response.status_code}: {response.text[:300]}")
        return response.json()

    def save_item(self, item_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """Upsert one item. Returns the parsed response body."""
        payload = {
            "dataCollectionId": self._collection_id,
            "dataItem": {"id": item_id, "data": data},
        }
        return self._post(SAVE_ITEM_URL, payload, what=f"Save Data Item for {item_id!r}")

    def save_items(self, items: dict[str, dict[str, Any]]) -> list[BulkItemResult]:
        """Upsert many items in batches of BULK_MAX_ITEMS. `items` maps
        item_id -> data. Returns one BulkItemResult per input item, in the
        order the caller's dict iterates — a failure on one item never
        blocks the others in its batch."""
        if not items:
            return []

        entries = list(items.items())
        results: list[BulkItemResult] = []
        for batch in _chunk(entries, BULK_MAX_ITEMS):
            batch_ids = [item_id for item_id, _ in batch]
            payload = {
                "dataCollectionId": self._collection_id,
                "dataItems": [{"id": item_id, "data": data} for item_id, data in batch],
            }
            try:
                body = self._post(BULK_SAVE_URL, payload, what="Bulk Save Data Items")
            except WixError as exc:
                # One bad batch (network blip, whole-request rejection)
                # shouldn't lose results already collected from prior
                # batches — surface every item in this batch as failed
                # instead of raising and discarding everything so far.
                results.extend(BulkItemResult(item_id=i, success=False, error=str(exc)) for i in batch_ids)
                continue
            for entry in body.get("results", []):
                meta = entry.get("itemMetadata") or {}
                idx = meta.get("originalIndex")
                item_id = batch_ids[idx] if idx is not None and idx < len(batch_ids) else meta.get("id", "?")
                error = meta.get("error")
                results.append(
                    BulkItemResult(
                        item_id=item_id,
                        success=bool(meta.get("success")),
                        error=str(error) if error else None,
                    )
                )
        return results

    def remove_items(self, item_ids: list[str]) -> list[BulkItemResult]:
        """Delete many items in batches of BULK_MAX_ITEMS."""
        if not item_ids:
            return []

        results: list[BulkItemResult] = []
        for batch in _chunk(item_ids, BULK_MAX_ITEMS):
            payload = {"dataCollectionId": self._collection_id, "dataItemIds": batch}
            try:
                body = self._post(BULK_REMOVE_URL, payload, what="Bulk Remove Data Items")
            except WixError as exc:
                results.extend(BulkItemResult(item_id=i, success=False, error=str(exc)) for i in batch)
                continue
            for entry in body.get("results", []):
                meta = entry.get("itemMetadata") or {}
                idx = meta.get("originalIndex")
                item_id = batch[idx] if idx is not None and idx < len(batch) else meta.get("id", "?")
                error = meta.get("error")
                results.append(
                    BulkItemResult(
                        item_id=item_id,
                        success=bool(meta.get("success")),
                        error=str(error) if error else None,
                    )
                )
        return results
