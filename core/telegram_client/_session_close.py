"""End sessions of the account that are not ours — one ``account.resetAuthorization`` per hash.

Never ``auth.resetAuthorizations`` ("terminate all others"): an account may hold a second
sign-in of its own (desktop AND mobile, each its own authorization), and that call would
end it. Only the hashes the operator named are touched, and only after the list is read
again: a hash that is no longer listed is already gone, and one that is ours is refused
whatever was asked.

Telegram refuses the call from a session younger than 24 hours
(``FRESH_RESET_AUTHORISATION_FORBIDDEN``, ``core.telegram.org/method/account.resetAuthorization``).
The imported seller session is old, so it can; a freshly signed-in one cannot. That refusal
ends the run — every later call would meet it too — and carries the time left when the
asking session's age is known.

One call per hash with a pause between them: ending a handful of sessions in one burst is
what an automated client does. Core holds no randomness, so the pauses arrive drawn.
"""

from __future__ import annotations

import asyncio
import math
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from telethon import errors
from telethon.tl.functions.account import ResetAuthorizationRequest

from core.config import settings
from core.telegram_client._pool import TelegramClientPoolError, get_client
from core.telegram_client._profile import _DEAD_SESSION_ERRORS
from core.telegram_client._session_audit import (
    SessionAuditGatewayCode,
    SessionAuditGatewayError,
    read_account_sessions,
)
from schemas.session_close import SessionCloseOutcome

if TYPE_CHECKING:
    from collections.abc import Sequence

    from schemas.device_fingerprint import DeviceType
    from schemas.session_audit import AccountSession
    from schemas.session_close import SessionCloseStatus

_UNAVAILABLE: SessionAuditGatewayCode = "unavailable"
# What Telegram's 406 means: the asking session has not been signed in for a day yet.
_FRESH_SESSION_WINDOW = timedelta(hours=24)


def _outcome(
    hash_: str,
    status: SessionCloseStatus,
    *,
    retry_after_seconds: int | None = None,
    error_type: str | None = None,
) -> SessionCloseOutcome:
    return SessionCloseOutcome(
        hash=hash_,
        status=status,
        retry_after_seconds=retry_after_seconds,
        error_type=error_type,
    )


def _seconds_until_old_enough(asking: AccountSession | None) -> int | None:
    """Seconds until the asking session is a day old; ``None`` when its age is not known."""
    if asking is None or asking.date_created is None:
        return None
    old_enough = datetime.fromisoformat(asking.date_created) + _FRESH_SESSION_WINDOW
    return max(1, math.ceil((old_enough - datetime.now(UTC)).total_seconds()))


async def _end_one(
    client: object, hash_: str, asking: AccountSession | None
) -> tuple[SessionCloseOutcome, bool]:
    """Close one session; the outcome, and whether the run goes on after it."""
    try:
        async with asyncio.timeout(settings.telegram.session_check_timeout_seconds):
            await client(ResetAuthorizationRequest(hash=int(hash_)))  # ty: ignore[call-non-callable]
    except errors.HashInvalidError:
        # Gone between the read and the call: what the operator wanted is done.
        return _outcome(hash_, "gone"), True
    except errors.FreshResetAuthorisationForbiddenError:
        return _outcome(
            hash_, "too_new", retry_after_seconds=_seconds_until_old_enough(asking)
        ), False
    except errors.FloodWaitError as exc:
        return _outcome(hash_, "flood_wait", retry_after_seconds=exc.seconds), False
    except (
        *_DEAD_SESSION_ERRORS,
        errors.RPCError,
        TelegramClientPoolError,
        OSError,
        TimeoutError,
    ) as exc:
        return _outcome(hash_, "failed", error_type=type(exc).__name__), False
    return _outcome(hash_, "closed"), True


async def close_sessions(
    account_id: str,
    devices: Sequence[DeviceType],
    hashes: Sequence[str],
    pauses: Sequence[float],
) -> list[SessionCloseOutcome]:
    """End the listed ``hashes`` that are still foreign; one outcome per hash, in order.

    The read before the first call is the only step that raises
    (:class:`SessionAuditGatewayError`): once a session has been ended the outcomes so far
    are the answer, so a later refusal stops the run and marks the rest ``skipped``.
    ``pauses[i]`` is waited before the ``i + 2``-nd call; fewer pauses than gaps means no
    wait for the extra ones. The asking client is the account's primary: the imported
    session, and the oldest one we hold.
    """
    listing = await read_account_sessions(account_id, devices)
    by_hash = {session.hash: session for session in listing.sessions}
    asking = next((session for session in listing.sessions if session.is_current), None)
    try:
        client = await get_client(account_id, devices[0])
    except (TelegramClientPoolError, ConnectionError, TimeoutError) as exc:
        raise SessionAuditGatewayError(_UNAVAILABLE, error_type=type(exc).__name__) from exc
    outcomes: list[SessionCloseOutcome] = []
    going = True
    calls = 0
    for hash_ in hashes:
        session = by_hash.get(hash_)
        if session is None:
            outcomes.append(_outcome(hash_, "gone"))
        elif session.is_ours:
            outcomes.append(_outcome(hash_, "not_foreign"))
        elif not going:
            outcomes.append(_outcome(hash_, "skipped"))
        else:
            if calls and calls <= len(pauses):
                await asyncio.sleep(pauses[calls - 1])
            calls += 1
            outcome, going = await _end_one(client, hash_, asking)
            outcomes.append(outcome)
    return outcomes
