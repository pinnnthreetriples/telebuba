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

ponytail: KNOWN RESIDUAL, deliberately not fixed in this PR. Nothing here or
anywhere else can clear the column on a DOWNGRADE. An older build boots fine
against a migrated database and simply ignores this column, so a deploy rolled
back "to be safe" leaves one orphaned plaintext password per account with no code
left in the process that can read or clear it. The fix is a pre-downgrade wipe
step, which needs a migration story this feature does not have yet.
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


def _set_account_twofa_password(account_id: str, password: str | None) -> bool:
    with _get_engine().begin() as connection:
        result = connection.execute(
            update(_accounts)
            .where(_accounts.c.account_id == account_id)
            .values(
                twofa_password=password,
                # A password that was verified, recorded or forgotten supersedes any
                # candidate staged for an earlier write.
                twofa_password_pending=None,
                twofa_pending_at=None,
                updated_at=_now_iso(),
            ),
        )
    return result.rowcount > 0


async def set_account_twofa_password(account_id: str, password: str | None) -> bool:
    """Remember (or clear, on ``None``) the cloud password we set for this account.

    ``None`` is the removal path: once 2FA is off, keeping the old password would
    be a stored secret guarding nothing. A missing account is a silent no-op, the
    same contract ``update_account_status`` has.

    Also drops the pending candidate (see :func:`set_account_twofa_pending`).

    Answers whether a row was actually written, because for this column silence is
    not good enough: the caller reports ``stored`` to the operator, and an
    ``UPDATE ... WHERE`` against an account deleted a moment earlier changes nothing
    and raises nothing. Without the ``rowcount`` the response would promise that the
    only copy of a cloud password is safe in a row that does not exist.
    """
    return await asyncio.to_thread(_set_account_twofa_password, account_id, password)


def _fetch_twofa_pair(account_id: str) -> tuple[str | None, str | None]:
    with _get_engine().connect() as connection:
        row = connection.execute(
            select(_accounts.c.twofa_password, _accounts.c.twofa_password_pending).where(
                _accounts.c.account_id == account_id,
            ),
        ).first()
    if row is None:
        return None, None
    return (str(row[0]) if row[0] else None, str(row[1]) if row[1] else None)


async def fetch_account_twofa_pair(account_id: str) -> tuple[str | None, str | None]:
    """``(current, pending)`` in ONE read; either is ``None`` when absent."""
    return await asyncio.to_thread(_fetch_twofa_pair, account_id)


async def twofa_password_candidates(account_id: str) -> list[str]:
    """Every cloud password that may be in force, the current one first.

    The login paths try these in order: the pending candidate is the one a write
    whose outcome is unknown (crash, lost reply) may have put on Telegram. An
    unknown account or no password gives ``[]``. Never log or return the values.
    """
    current, pending = await fetch_account_twofa_pair(account_id)
    return [value for value in (current, pending) if value]


def _set_twofa_pending(account_id: str, password: str | None) -> bool:
    with _get_engine().begin() as connection:
        result = connection.execute(
            update(_accounts)
            .where(_accounts.c.account_id == account_id)
            .values(
                twofa_password_pending=password,
                twofa_pending_at=_now_iso() if password else None,
                updated_at=_now_iso(),
            ),
        )
    return result.rowcount > 0


async def set_account_twofa_pending(account_id: str, password: str | None) -> bool:
    """Stage (or clear, on ``None``) the candidate a write is about to send.

    Must be durable BEFORE the RPC: callers refuse to send when this answers ``False``
    (an unknown account counts, like a failed write).
    """
    return await asyncio.to_thread(_set_twofa_pending, account_id, password)


def _promote_twofa_pending(account_id: str, password: str) -> bool:
    # Compare-and-swap: the single UPDATE names the value being promoted, so a pending
    # candidate that changed between the caller's read and this write is left alone.
    with _get_engine().begin() as connection:
        result = connection.execute(
            update(_accounts)
            .where(
                _accounts.c.account_id == account_id,
                _accounts.c.twofa_password_pending == password,
            )
            .values(
                twofa_password=password,
                twofa_password_pending=None,
                twofa_pending_at=None,
                updated_at=_now_iso(),
            ),
        )
    return result.rowcount > 0


async def promote_account_twofa_pending(account_id: str, password: str) -> bool:
    """``password`` becomes the current one IF it is still the pending one, atomically.

    One conditional ``UPDATE ... WHERE twofa_password_pending = :password``; ``False``
    when the pending candidate is absent or a different value.
    """
    return await asyncio.to_thread(_promote_twofa_pending, account_id, password)


def _set_twofa_current_only(account_id: str, password: str) -> bool:
    with _get_engine().begin() as connection:
        result = connection.execute(
            update(_accounts)
            .where(_accounts.c.account_id == account_id)
            .values(twofa_password=password, updated_at=_now_iso()),
        )
    return result.rowcount > 0


async def set_account_twofa_current_only(account_id: str, password: str) -> bool:
    """Store ``password`` as the current one WITHOUT touching a pending candidate.

    For a password Telegram just accepted at sign-in: unlike
    :func:`set_account_twofa_password` it never clears the staged candidate, which may
    be the one in force.
    """
    return await asyncio.to_thread(_set_twofa_current_only, account_id, password)


async def confirm_twofa_password_used(account_id: str, password: str) -> bool:
    """A login or verify succeeded with ``password``: promote it if it was the pending one.

    Call after a successful 2FA sign-in so a candidate that turned out to be in force
    stops being "pending". A no-op (``False``) when ``password`` is the current one or
    matches nothing. The promotion is one compare-and-swap, so a pending value that
    changed since the read is never promoted in its place.
    """
    return await asyncio.to_thread(_promote_twofa_pending, account_id, password)
