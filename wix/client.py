"""Wix CMS transport: a single Save Data Item call.

Shape confirmed against the Wix Data v2 reference (Save Data Item):
    POST https://www.wixapis.com/wix-data/v2/items/save
    {"dataCollectionId": "...", "dataItem": {"id": "...", "data": {...}}}

Save upserts on `dataItem.id` — it creates when the ID is new and updates
when the ID already exists — which is why publishing is safe to re-run.
"""

from __future__ import annotations

from typing import Any

import requests

SAVE_ITEM_URL = "https://www.wixapis.com/wix-data/v2/items/save"


class WixError(Exception):
    """A Save Data Item call failed."""


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

    def save_item(self, item_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """Upsert one item. Returns the parsed response body."""
        payload = {
            "dataCollectionId": self._collection_id,
            "dataItem": {"id": item_id, "data": data},
        }
        try:
            response = requests.post(
                SAVE_ITEM_URL,
                json=payload,
                headers={
                    "Authorization": self._api_key,
                    "wix-site-id": self._site_id,
                    "Content-Type": "application/json",
                },
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise WixError(f"Save Data Item request failed for {item_id!r}: {exc}") from exc

        if response.status_code >= 400:
            raise WixError(
                f"Save Data Item returned {response.status_code} for {item_id!r}: "
                f"{response.text[:300]}"
            )
        return response.json()
