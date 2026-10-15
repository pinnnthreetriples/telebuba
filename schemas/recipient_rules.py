"""Who a bulk-message run may write to, judged by what earlier runs sent.

Shared by the saved outreach limits (the defaults) and each run request (its own
copy). The history behind it (``core.repositories.recipient_history``) holds only a
recipient key, the account and the time of each first message the bulk tool sent.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

MAX_NO_REPEAT_DAYS = 365


class RecipientRules(BaseModel):
    # Skip a recipient any account messaged within this many days; ``None`` turns it off.
    no_repeat_days: int | None = Field(default=30, ge=1, le=MAX_NO_REPEAT_DAYS)
    # A recipient is only ever written to by the account that wrote to them first.
    one_account_per_client: bool = True
    # A number the contact lookup reported "not on Telegram" is checked again the
    # next UTC day by another account.
    recheck_not_found: bool = True
