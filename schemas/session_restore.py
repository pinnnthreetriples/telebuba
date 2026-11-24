"""What the session vault holds for one account, for the restore button."""

from __future__ import annotations

from pydantic import BaseModel


class SessionCopies(BaseModel):
    """How many copies of the account's own session exist and whether a restore is offered."""

    vault_enabled: bool
    count: int = 0
    # ISO time of the newest copy, from its file name.
    newest_at: str | None = None
    # The vault is on, a copy exists and the account's file is missing or holds no key.
    can_restore: bool = False
