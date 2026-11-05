"""Telebuba's own Telegram Web login is ours: record it so the audit never calls it foreign.

Signing the account in to Telegram Web (a per-account Chrome: a QR accepted through
``AcceptLoginTokenRequest``, or a passkey) creates an authorization of the account that is not
one of its Telethon devices, so asking our clients (``core.telegram_client``'s audit) cannot
tell it from a stranger's. Telling it from a stranger's is the whole difficulty: a seller or
thief logging in to Telegram Web in the same minute looks identical, and a wrongly recorded
row is one the audit hides and the close refuses to end.

So the window is judged by what it can PROVE, never by a clock. When the QR login was
accepted through ``AcceptLoginTokenRequest`` Telegram names the authorization it created: that
hash is recorded (when it is listed as a Web one). Otherwise, when a window is launched for an
unsigned profile, :func:`remember_login_baseline` keeps the hashes the account listed at that
moment, and :func:`record_own_web_login` records a Web authorization as ours only if it is
NEW against that baseline and is the ONLY such one: two candidates mean somebody else logged
in too, and nothing is recorded (a warning says so). With neither an accepted hash nor a
baseline (a hand-finished login, an unreadable list) nothing is recorded: a guess could hide
a stranger's session.

The gateway function is imported at module scope so tests monkeypatch it at
``services.accounts.own_authorizations.<name>``.
"""

from __future__ import annotations

import weakref
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.logging import log_event
from core.repositories.account_own_authorizations import (
    fetch_own_authorizations,
    save_own_authorizations,
)
from core.repositories.session_audits import fetch_session_audit, save_session_audit
from core.telegram_client import SessionAuditGatewayError, read_account_sessions
from services.accounts._result import AccountActionError
from services.accounts._session_classify import with_own
from services.accounts.session_audit import account_audit_lock, require_proxied_account

if TYPE_CHECKING:
    from core.web_login.browser import WebWindow
    from schemas.session_audit import AccountSession, AuthorizationList

__all__ = [
    "forget_login_baseline",
    "record_own_web_login",
    "remember_login_baseline",
]

_KIND = "web"
# A seller on a desktop/mobile app in the same window must never become ours.
_KIND_MARK = "web"
# Authorization hashes the account listed when each window was launched, kept with the window
# (and gone with it).
_baselines: weakref.WeakKeyDictionary[WebWindow, frozenset[str]] = weakref.WeakKeyDictionary()


async def _list_sessions(account_id: str) -> tuple[AuthorizationList, dict[str, str]]:
    """The account's authorizations (the caller holds the audit lock) and its recorded own ones."""
    account = await require_proxied_account(account_id)
    known = await fetch_own_authorizations(account_id)
    try:
        return await read_account_sessions(account_id, account.devices, known), known
    except SessionAuditGatewayError as exc:
        await log_event(
            "WARNING",
            "own_authorizations_failed",
            account_id=account_id,
            extra={"code": exc.code, "error_type": exc.error_type},
        )
        raise AccountActionError(exc.code, retry_after_seconds=exc.retry_after_seconds) from exc


async def remember_login_baseline(account_id: str, window: WebWindow) -> bool:
    """Keep the account's current authorization hashes with ``window``; False when unreadable."""
    # A read-only listing: no ``account_audit_lock``, which a running session close holds
    # through its paced pauses and would starve the launch's bounded wait for this.
    try:
        listing, _known = await _list_sessions(account_id)
    except AccountActionError:
        _baselines.pop(window, None)
        return False
    _baselines[window] = frozenset(session.hash for session in listing.sessions)
    return True


def forget_login_baseline(window: WebWindow) -> None:
    """Drop ``window``'s baseline (the login it was kept for has been settled)."""
    _baselines.pop(window, None)


def _is_web_candidate(session: AccountSession) -> bool:
    return (
        not session.is_current and not session.is_ours and _KIND_MARK in session.app_name.casefold()
    )


def _candidates(
    sessions: list[AccountSession], baseline: frozenset[str] | None, accepted_hash: str | None
) -> list[str]:
    """Hashes of the Web authorizations this login may have made (none without proof)."""
    web = [session for session in sessions if _is_web_candidate(session)]
    if accepted_hash:
        return [session.hash for session in web if session.hash == accepted_hash]
    if baseline is None:
        return []
    return [session.hash for session in web if session.hash not in baseline]


async def record_own_web_login(
    account_id: str, window: WebWindow, *, accepted_hash: str | None = None
) -> int:
    """Record the web login ``window`` just completed as ours; how many rows were recorded.

    ``accepted_hash`` is the authorization Telegram returned when the QR token was accepted:
    when known, exactly that one is recorded. Otherwise only the ONE Web authorization new
    against the window's baseline is; zero, several or no baseline record nothing. Raises
    :class:`AccountActionError` when the list cannot be read (the gateway's code), keeping the
    baseline for a later drive.
    """
    baseline = _baselines.get(window)
    async with account_audit_lock(account_id):
        listing, known = await _list_sessions(account_id)
        fresh = _candidates(listing.sessions, baseline, accepted_hash)
        if len(fresh) == 1:
            now = datetime.now(UTC).isoformat()
            await save_own_authorizations(account_id, fresh, kind=_KIND, recorded_at=now)
            await _refresh_snapshot(account_id, {**known, **dict.fromkeys(fresh, _KIND)})
    if fresh:  # a login that showed nothing new yet keeps its baseline for the next drive
        forget_login_baseline(window)
    extra: dict[str, object] = {
        "candidates": len(fresh),
        "listed": len(listing.sessions),
        "baseline": baseline is not None,
        "accepted_hash": bool(accepted_hash),
    }
    if len(fresh) == 1:
        await log_event(
            "INFO", "own_web_authorizations_recorded", account_id=account_id, extra=extra
        )
        return 1
    # Zero: nothing provable (yet); several: somebody else signed in too. Either way
    # recording a guess would hide a stranger's session, so nothing is.
    await log_event("WARNING", "own_web_login_not_recorded", account_id=account_id, extra=extra)
    return 0


async def _refresh_snapshot(account_id: str, own: dict[str, str]) -> None:
    """Re-mark the saved audit so the card and the table badge stop counting what is ours."""
    saved = await fetch_session_audit(account_id)
    if saved is None:
        return
    updated = with_own(saved, own)
    if updated.foreign_count != saved.foreign_count:
        await save_session_audit(updated)
