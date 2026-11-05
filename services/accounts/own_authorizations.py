"""Telebuba's own Telegram Web login is ours: record it so the audit never calls it foreign.

Signing the account in to Telegram Web (a per-account Chrome: a QR accepted through
``AcceptLoginTokenRequest``, or a passkey) creates an authorization of the account that is not
one of its Telethon devices, so asking our clients (``core.telegram_client``'s audit) cannot
tell it from a stranger's. Right after such a login the caller hands the moment the login
started to :func:`record_own_web_authorizations`, which lists the authorizations with the
account's primary pooled client and records every non-current, not-already-ours one created
since then as ours (``account_own_authorizations``). From then on the audit lists it as
"Telebuba's Telegram Web", the foreign count excludes it and the close never names it.

The gateway function is imported at module scope so tests monkeypatch it at
``services.accounts.own_authorizations.<name>``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

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

__all__ = ["record_own_web_authorizations"]

_KIND = "web"


async def record_own_web_authorizations(account_id: str, since: datetime) -> int:
    """Record the web login made since ``since`` as ours; how many authorizations were new.

    Considers every listed authorization that is not the asking session, not already ours
    (a device of the account or one recorded earlier) and was created no earlier than
    ``since`` minus the configured skew. A naive ``since`` is read as UTC. Raises
    :class:`AccountActionError` when the list cannot be read (the gateway's code).
    """
    account = await require_proxied_account(account_id)
    since = since if since.tzinfo is not None else since.replace(tzinfo=UTC)
    threshold = since - timedelta(seconds=settings.session_audit.own_authorization_skew_seconds)
    async with account_audit_lock(account_id):
        known = await fetch_own_authorizations(account_id)
        try:
            listing = await read_account_sessions(account_id, account.devices, known)
        except SessionAuditGatewayError as exc:
            await log_event(
                "WARNING",
                "own_authorizations_failed",
                account_id=account_id,
                extra={"code": exc.code, "error_type": exc.error_type},
            )
            raise AccountActionError(exc.code, retry_after_seconds=exc.retry_after_seconds) from exc
        fresh = [
            session.hash
            for session in listing.sessions
            if not session.is_current
            and not session.is_ours
            and (created := parse_moment(session.date_created)) is not None
            and created >= threshold
        ]
        now = datetime.now(UTC).isoformat()
        await save_own_authorizations(account_id, fresh, kind=_KIND, recorded_at=now)
        await _refresh_snapshot(account_id, {**known, **dict.fromkeys(fresh, _KIND)})
    await log_event(
        "INFO",
        "own_web_authorizations_recorded",
        account_id=account_id,
        extra={"recorded": len(fresh), "listed": len(listing.sessions)},
    )
    return len(fresh)


async def _refresh_snapshot(account_id: str, own: dict[str, str]) -> None:
    """Re-mark the saved audit so the card and the table badge stop counting what is ours."""
    saved = await fetch_session_audit(account_id)
    if saved is None:
        return
    updated = with_own(saved, own)
    if updated.foreign_count != saved.foreign_count:
        await save_session_audit(updated)
