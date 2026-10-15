"""Rows of ``recipient_history`` and ``contact_rechecks``."""

from __future__ import annotations

from pydantic import BaseModel


class RecipientContact(BaseModel):
    """One first message the bulk tool sent (or may have sent) to a recipient."""

    # A Telegram user id in canonical digits, or a username casefolded without "@".
    peer: str
    # The phone the recipient was found by, digits only.
    phone: str | None = None
    account_id: str
    sent_at: str


class ContactRecheck(BaseModel):
    """A number a lookup answered "not on Telegram", queued for another account."""

    phone_key: str
    phone: str
    checked_by: str
    due_at: str
    created_at: str
