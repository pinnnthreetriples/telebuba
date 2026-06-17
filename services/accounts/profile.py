"""Profile-field updates (name / username / bio) for the accounts domain.

``execute`` is imported at module scope so tests can monkeypatch
``services.accounts.profile.execute``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.db import update_account_profile_snapshot
from core.logging import log_event
from core.telegram_client import execute
from schemas.telegram_actions import UpdateProfile

if TYPE_CHECKING:
    from schemas.accounts import AccountProfileUpdateRequest, AccountRead

__all__ = ["update_account_profile"]


async def update_account_profile(data: AccountProfileUpdateRequest) -> AccountRead:
    result = await execute(
        data.account_id,
        UpdateProfile(
            first_name=data.first_name,
            last_name=data.last_name,
            username=data.username,
            bio=data.bio,
        ),
    )
    if result.status != "ok":
        msg = result.error_message or result.status
        raise ValueError(msg)
    account = await update_account_profile_snapshot(data)
    await log_event(
        "INFO",
        "account_profile_updated",
        account_id=data.account_id,
        extra={
            "has_last_name": data.last_name is not None,
            "has_username": data.username is not None,
            "has_bio": data.bio is not None,
        },
    )
    return account
