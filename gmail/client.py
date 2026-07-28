"""Gmail draft creation via domain-wide delegation on the Sheets service
account (scoped to gmail.compose only — this client never sends).
"""

from __future__ import annotations

import base64
from email.mime.text import MIMEText

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

_SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]


class GmailClient:
    def __init__(self, *, credentials_path: str, sender: str) -> None:
        creds = Credentials.from_service_account_file(
            credentials_path, scopes=_SCOPES
        ).with_subject(sender)
        self._service = build("gmail", "v1", credentials=creds)
        self._sender = sender

    def create_draft(self, *, to: str, subject: str, body: str) -> str:
        message = MIMEText(body)
        message["to"] = to
        message["from"] = self._sender
        message["subject"] = subject
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

        draft = (
            self._service.users()
            .drafts()
            .create(userId="me", body={"message": {"raw": raw}})
            .execute()
        )
        draft_id = draft["id"]
        # Best-effort deep link — opens Gmail with the draft list; the human
        # reviewer can locate the exact draft from there if this doesn't
        # resolve directly in their client.
        return f"https://mail.google.com/mail/u/0/#drafts?compose={draft_id}"
