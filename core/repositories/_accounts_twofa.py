"""Cloud-password persistence (a sibling of ``core.repositories.accounts``).

Its own module, not two more functions in ``accounts.py``: that file is at the
per-file size budget, and keeping the plaintext column in a module of its own
makes the reachable surface auditable — these two functions are the ONLY code
that touches ``accounts.twofa_password``.

Why the password is stored at all: a Telegram account whose session is reset
needs its cloud password to finish ``submit_phone_code``. Without a stored copy
nobody, the operator included, can log that account back in. The precedent in
this repo is ``proxies.password``, kept in the clear in the same SQLite file
under the same 0700/0600 permissions.

Why the plaintext leaves only through :func:`fetch_account_twofa_password`:
``_row_to_account`` and ``_account_select_statement`` do not name the column, so
no account read model can carry it — exactly the arrangement
``core.repositories.proxies`` uses, where ``_row_to_proxy`` maps the stored
secret to ``has_password=bool(...)`` and a separate, non-API-facing mapper is the
only thing that resolves it.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select, update

from core.db import _accounts, _get_engine, _now_iso


def _fetch_account_twofa_password(account_id: str) -> str | None:
    with _get_engine().connect() as connection:
        row = connection.execute(
            select(_accounts.c.twofa_password).where(_accounts.c.account_id == account_id),
        ).first()
    if row is None:
        return None
    value = row[0]
    return str(value) if value else None


async def fetch_account_twofa_password(account_id: str) -> str | None:
    """The stored cloud password, or ``None`` for an unknown account / no password.

    The one seam the plaintext crosses. Callers use it to authorise a change or a
    removal, and to answer ``has_stored_password`` as a boolean; the value itself
    must never reach a response other than the POST that created it, a log event
    or an error message.
    """
    return await asyncio.to_thread(_fetch_account_twofa_password, account_id)


def _set_account_twofa_password(account_id: str, password: str | None) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            update(_accounts)
            .where(_accounts.c.account_id == account_id)
            .values(twofa_password=password, updated_at=_now_iso()),
        )


async def set_account_twofa_password(account_id: str, password: str | None) -> None:
    """Remember (or clear, on ``None``) the cloud password we set for this account.

    ``None`` is the removal path: once 2FA is off, keeping the old password would
    be a stored secret guarding nothing. A missing account is a silent no-op, the
    same contract ``update_account_status`` has.
    """
    await asyncio.to_thread(_set_account_twofa_password, account_id, password)
