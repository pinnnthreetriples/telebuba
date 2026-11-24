"""The phone app's own authorization: recorded as ours, never silently re-created.

The phone sign-in (``_emulator_sign_in``) creates an authorization of the account that no
Telethon client of it is, so the audit would call it foreign, the watch would alert, and the
operator could close it. Like the web login (``own_authorizations``) it is judged by what it
can PROVE: the hashes the account listed BEFORE the phone number was submitted are the
baseline, and after the chat list shows, the ONE authorization that is new against it, is
not ours or the asking session, and is an official Android app is recorded as ``phone``.
Zero or several candidates record nothing (a stranger may have logged in too).

The row is also what tells a signed-out app from a first sign-in. An app that shows an
entry screen while a ``phone`` authorization is recorded lost its data (or was ended on
purpose): the row is forgotten, and when Telegram still lists that authorization it is now
truthfully foreign (``phone_authorization_orphaned``). Warming never signs it in again
(``emulator_signed_out``, kept across cycles and restarts by a mark in ``pending_cleanups``);
only the operator's own open does, and clears the mark.

Every read of Telegram here is best effort: a failure never fails the sign-in.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.emulator import EmulatorError
from core.logging import log_event
from core.repositories.account_own_authorizations import (
    fetch_own_authorizations,
    forget_own_authorization,
    save_own_authorizations,
)
from core.repositories.pending_cleanups import (
    add_pending_cleanup,
    clear_pending_cleanup,
    fetch_pending_cleanups,
)
from services.accounts.own_authorizations import _list_sessions, _refresh_snapshot
from services.accounts.session_audit import account_audit_lock

if TYPE_CHECKING:
    from schemas.emulator import EmulatorRefusalCode
    from schemas.session_audit import AccountSession

__all__ = ["PhoneSignIn", "settle_recorded_authorization"]

KIND = "phone"
# ``pending_cleanups`` kind: accounts whose app warming found signed out and must not sign in.
SIGNED_OUT_MARK = "phone_signed_out"
_EMULATOR_SIGNED_OUT: EmulatorRefusalCode = "emulator_signed_out"
# Recording is bookkeeping after a sign-in that worked: it never holds that sign-in up long.
_RECORD_TIMEOUT_SECONDS = 60.0


def _is_candidate(session: AccountSession, baseline: frozenset[str]) -> bool:
    return (
        session.hash not in baseline
        and not session.is_current
        and not session.is_ours
        and session.official_app
        and "android" in session.platform.casefold()
    )


async def _listed_hashes(account_id: str) -> frozenset[str] | None:
    """The hashes the account lists now; ``None`` when the list cannot be read."""
    try:
        listing, _known = await _list_sessions(account_id)
    except Exception:  # noqa: BLE001 - best effort; ``_list_sessions`` logged what it knew
        return None
    return frozenset(session.hash for session in listing.sessions)


class PhoneSignIn:
    """One phone sign-in's bookkeeping: the baseline before the number, the record after."""

    def __init__(self, account_id: str) -> None:
        self.account_id = account_id
        self._submitted = False
        self._baseline: frozenset[str] | None = None

    async def take_baseline(self) -> None:
        """Remember the account's authorizations; call right BEFORE the number is submitted."""
        if not self._submitted:
            self._submitted = True
            self._baseline = await _listed_hashes(self.account_id)

    async def record(self) -> None:
        """Record the authorization this sign-in made, if it can be told; never raises."""
        if not self._submitted:
            return  # the app was signed in already: nothing was made
        try:
            async with asyncio.timeout(_RECORD_TIMEOUT_SECONDS):
                await self._record()
        except Exception as exc:  # noqa: BLE001 - the sign-in worked; bookkeeping must not fail it
            await log_event(
                "WARNING",
                "own_phone_login_not_recorded",
                account_id=self.account_id,
                extra={"candidates": 0, "error_type": type(exc).__name__},
            )

    async def _record(self) -> None:
        account_id = self.account_id
        fresh: list[str] = []
        async with account_audit_lock(account_id):
            listing, known = await _list_sessions(account_id)
            if self._baseline is not None:
                fresh = [s.hash for s in listing.sessions if _is_candidate(s, self._baseline)]
            if len(fresh) == 1:
                now = datetime.now(UTC).isoformat()
                await save_own_authorizations(account_id, fresh, kind=KIND, recorded_at=now)
                await _refresh_snapshot(account_id, {**known, fresh[0]: KIND})
        if len(fresh) == 1:
            await log_event(
                "INFO",
                "own_phone_authorization_recorded",
                account_id=account_id,
                extra={"hash_suffix": fresh[0][-4:]},
            )
            return
        # Zero (nothing provable) or several (somebody else signed in too): recording a
        # guess could hide a stranger's session, so nothing is.
        await log_event(
            "WARNING",
            "own_phone_login_not_recorded",
            account_id=account_id,
            extra={"candidates": len(fresh), "baseline": self._baseline is not None},
        )


async def _signed_out_mark(account_id: str) -> bool:
    """Whether warming was already refused this app; unreadable is "no" (a first sign-in)."""
    try:
        return account_id in await fetch_pending_cleanups(SIGNED_OUT_MARK)
    except Exception:  # noqa: BLE001 - bookkeeping; the sign-in rules below still hold
        return False


async def _set_signed_out_mark(account_id: str, *, on: bool) -> None:
    try:
        if on:
            now = datetime.now(UTC).isoformat()
            await add_pending_cleanup(SIGNED_OUT_MARK, account_id, requested_at=now)
        else:
            await clear_pending_cleanup(SIGNED_OUT_MARK, account_id)
    except Exception as exc:  # noqa: BLE001 - never a reason to fail the open
        await log_event(
            "WARNING",
            "phone_signed_out_mark_failed",
            account_id=account_id,
            extra={"error_type": type(exc).__name__},
        )


async def settle_recorded_authorization(account_id: str, *, background: bool) -> None:
    """The app shows an entry screen: forget the ``phone`` authorization recorded for it.

    One listing, best effort, tells whether Telegram still lists it. Still alive: the
    container lost its data, and the authorization is shown foreign from now on. In the
    background (warming) the app is then NOT signed in again: ``emulator_signed_out``,
    now and on every later cycle (a mark outlives the forgotten row and a restart), until
    the operator's own open signs it in. Nothing recorded and no mark: a first sign-in,
    which goes ahead as ever.
    """
    recorded = [
        h for h, kind in (await fetch_own_authorizations(account_id)).items() if kind == KIND
    ]
    marked = await _signed_out_mark(account_id)
    if not recorded and not marked:
        return
    alive = (await _listed_hashes(account_id) or frozenset()) if recorded else frozenset()
    for hash_ in recorded:
        await forget_own_authorization(account_id, hash_)
        if hash_ in alive:
            await log_event(
                "WARNING",
                "phone_authorization_orphaned",
                account_id=account_id,
                extra={"hash_suffix": hash_[-4:]},
            )
    if background:
        if not marked:
            await _set_signed_out_mark(account_id, on=True)
            await log_event("WARNING", "phone_signed_out", account_id=account_id)
        raise EmulatorError(_EMULATOR_SIGNED_OUT)
    if marked:
        await _set_signed_out_mark(account_id, on=False)
