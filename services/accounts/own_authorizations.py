"""Telebuba's own Telegram Web login is ours: record it so the audit never calls it foreign.

Signing the account in to Telegram Web (a per-account Chrome: a QR accepted through
``AcceptLoginTokenRequest``, or a passkey) creates an authorization of the account that is not
one of its Telethon devices, so asking our clients (``core.telegram_client``'s audit) cannot
tell it from a stranger's. Telling it from a stranger's is the whole difficulty: a seller or
thief logging in to Telegram Web in the same minute looks identical, and a wrongly recorded
row is one the audit hides and the close refuses to end.

So the window is judged by what CHANGED, not by a clock. When a window is launched for an
unsigned profile, :func:`remember_login_baseline` keeps the hashes the account listed at that
moment with the window. When any drive of that window then sees the logged-in screen,
:func:`record_own_web_login` lists again and records a Web authorization as ours only if it is
NEW against that baseline and is the ONLY such one: two candidates mean somebody else logged
in too, and nothing is recorded (a warning says so). Without a baseline (the read failed) the
fallback is a bounded time window, still with the exactly-one rule.

The gateway function is imported at module scope so tests monkeypatch it at
``services.accounts.own_authorizations.<name>``.
"""

from __future__ import annotations

import weakref
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.logging import log_event
from core.repositories.account_own_authorizations import (
    fetch_own_authorizations,
    save_own_authorizations,
)
from core.repositories.session_audits import fetch_session_audit, save_session_audit
from core.telegram_client import SessionAuditGatewayError, read_account_sessions
from services.accounts._result import AccountActionError
from services.accounts._session_classify import parse_moment, with_own
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
    try:
        async with account_audit_lock(account_id):
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
    sessions: list[AccountSession],
    baseline: frozenset[str] | None,
    since: datetime,
    seen_at: datetime,
) -> list[str]:
    """Hashes of the Web authorizations this login may have made."""
    skew = timedelta(seconds=settings.session_audit.own_login_skew_seconds)
    found: list[str] = []
    for session in sessions:
        if not _is_web_candidate(session):
            continue
        if baseline is not None:
            if session.hash not in baseline:
                found.append(session.hash)
            continue
        created = parse_moment(session.date_created)
        if created is not None and since - skew <= created <= seen_at + skew:
            found.append(session.hash)
    return found


async def record_own_web_login(
    account_id: str, window: WebWindow, *, since: datetime, seen_at: datetime
) -> int:
    """Record the web login ``window`` just completed as ours; how many rows were recorded.

    ``since`` (the drive's start) and ``seen_at`` (when the logged-in screen was read) bound
    the time-window fallback used only when ``window`` has no baseline. Exactly one candidate
    is recorded; zero or several record nothing. Raises :class:`AccountActionError` when the
    list cannot be read (the gateway's code), keeping the baseline for a later drive.
    """
    since = since if since.tzinfo is not None else since.replace(tzinfo=UTC)
    seen_at = seen_at if seen_at.tzinfo is not None else seen_at.replace(tzinfo=UTC)
    baseline = _baselines.get(window)
    async with account_audit_lock(account_id):
        listing, known = await _list_sessions(account_id)
        fresh = _candidates(listing.sessions, baseline, since, seen_at)
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
    }
    if len(fresh) == 1:
        await log_event(
            "INFO", "own_web_authorizations_recorded", account_id=account_id, extra=extra
        )
        return 1
    # Zero: nothing new is visible (yet); several: somebody else signed in too. Either way
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
