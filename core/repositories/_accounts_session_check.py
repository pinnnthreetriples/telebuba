"""What a session check writes back (a sibling of ``core.repositories.accounts``).

Its own module, not more lines in ``accounts.py``: that file is at the per-file
size budget. The grouping is not arbitrary — this is the only write path that
learns an account's phone from Telegram, which makes it the only place a device
fingerprint's language may be corrected, and the two commit together here.

``update_account_from_session_check`` is re-imported by
``core.repositories.accounts`` and re-exported by ``core.db``, so every existing
call site keeps working unchanged.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import TYPE_CHECKING

from sqlalchemy import update

from core.db import _accounts, _get_engine, _now_iso
from core.repositories._device_fingerprint_language import _correct_fingerprint_language

if TYPE_CHECKING:
    from schemas.accounts import AccountRead
    from schemas.telegram_session import TelegramSessionCheckResult


def _update_account_from_session_check(result: TelegramSessionCheckResult) -> AccountRead:
    from core.repositories.accounts import _fetch_account  # noqa: PLC0415

    now = _now_iso()
    values: dict[str, object] = {
        "status": result.status,
        "last_checked_at": now,
        "updated_at": now,
    }
    if result.status == "alive":
        values.update(
            {
                "user_id": result.user_id,
                "phone": result.phone,
                "username": result.username,
                "first_name": result.first_name,
                "last_name": result.last_name,
                "premium": result.premium,
            },
        )
        # Only overwrite the avatar when the check actually returned bytes — a
        # refused/absent download (None) must not wipe a good cached photo.
        if result.avatar_thumb is not None:
            values["avatar_thumb"] = result.avatar_thumb
            values["avatar_etag"] = hashlib.blake2b(result.avatar_thumb, digest_size=16).hexdigest()

    with _get_engine().begin() as connection:
        connection.execute(
            update(_accounts).where(_accounts.c.account_id == result.account_id).values(**values),
        )
        if result.status == "alive":
            # The one moment a fingerprint's language may be corrected, and the
            # reason it is here rather than at a service call site: this line is
            # reached only with a phone Telegram just returned, and every path
            # that has one — phone login and both import flows — arrives through
            # this function. In ``services.accounts.sessions`` it would have
            # missed ``services.accounts.login``, and left a fourth caller free
            # to skip it.
            #
            # The import paths mint the fingerprint BEFORE any connection exists
            # (the connection needs it), so they are born on the ``en-US``
            # fallback and this repairs it once. It also repairs a phone login
            # whose operator-typed number was unparseable, because the number
            # here is Telegram's canonical form and not the typed one.
            _correct_fingerprint_language(connection, result.account_id, result.phone)

    account = _fetch_account(result.account_id)
    if account is None:
        msg = f"Account not found: {result.account_id}"
        raise RuntimeError(msg)
    return account


async def update_account_from_session_check(result: TelegramSessionCheckResult) -> AccountRead:
    return await asyncio.to_thread(_update_account_from_session_check, result)
