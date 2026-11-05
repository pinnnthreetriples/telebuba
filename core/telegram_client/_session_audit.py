"""Read the authorizations Telegram lists for an account, and tell ours from the rest.

An account holds one or two sign-ins of its own (desktop and/or mobile, each its own
``.session``), and Telegram lists every session of the account from any one of them. The
list does not say which entries are ours — only the session that asked is marked
``current``. So each of the account's own clients is asked for the list and names ITS
authorization; the union of those is "ours", and everything else is somebody else's. What
a session looks like (device model, app name) decides nothing: an imported seller session
presents the seller's own device by design, so a look-alike is exactly the case a
heuristic would hide.

If any one of the account's clients cannot answer, the whole read fails. A partial answer
would list the unreachable device's own session as foreign, and a later "close foreign
sessions" would then log our own second device out.

Both calls borrow the pooled clients (the pool owns every ``.session``), so this sits
beside ``_passkey`` rather than in the typed-action unions: the result needs two
connections, which neither dispatcher models. Every failure becomes
:class:`SessionAuditGatewayError` with a bounded code, so ``services`` never sees a
Telethon error, and a dead session reports the same ``session_dead`` / ``account_deactivated``
codes the action gateway does.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Literal, cast

from telethon import errors
from telethon.tl.functions.account import GetAuthorizationsRequest

from core.config import settings
from core.telegram_client._pool import TelegramClientPoolError, get_client
from core.telegram_client._profile import _DEAD_SESSION_ERRORS, _dead_session_code
from schemas.session_audit import AccountSession, AuthorizationList

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from telethon import TelegramClient

    from schemas.device_fingerprint import DeviceType

SessionAuditGatewayCode = Literal[
    "session_dead",
    "account_deactivated",
    "account_frozen",
    "flood_wait",
    "unavailable",
    "session_audit_failed",
]

_FAILED: SessionAuditGatewayCode = "session_audit_failed"
_FROZEN: SessionAuditGatewayCode = "account_frozen"
_FLOOD: SessionAuditGatewayCode = "flood_wait"
_UNAVAILABLE: SessionAuditGatewayCode = "unavailable"


class SessionAuditGatewayError(Exception):
    """The audit could not read the list; ``str(exc)`` is the stable code, never Telethon prose."""

    def __init__(
        self,
        code: SessionAuditGatewayCode,
        *,
        retry_after_seconds: int | None = None,
        error_type: str | None = None,
    ) -> None:
        super().__init__(code)
        self.code: SessionAuditGatewayCode = code
        self.retry_after_seconds = retry_after_seconds
        self.error_type = error_type


def _refusal(
    code: SessionAuditGatewayCode,
    *,
    retry_after_seconds: int | None = None,
    error_type: str | None = None,
) -> SessionAuditGatewayError:
    return SessionAuditGatewayError(
        code, retry_after_seconds=retry_after_seconds, error_type=error_type
    )


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _own_hash(authorizations: Sequence[object]) -> int:
    """The hash of the entry the asking client is — the one Telegram marks ``current``."""
    for authorization in authorizations:
        if getattr(authorization, "current", None):
            hash_ = getattr(authorization, "hash", None)
            if isinstance(hash_, int):
                return hash_
    # Our own session missing from its own list: nothing here can be trusted to say who
    # is foreign, so the audit must not guess.
    raise _refusal(_FAILED, error_type="OwnSessionNotListed")


def _session(
    authorization: object, *, current: int, ours: frozenset[int], own: Mapping[str, str]
) -> AccountSession:
    hash_ = getattr(authorization, "hash", None)
    if not isinstance(hash_, int):
        raise _refusal(_FAILED, error_type="HashMissing")
    # A recorded own authorization (Telebuba's web login) is ours too, with its kind.
    kind = own.get(str(hash_)) if hash_ not in ours else None
    return AccountSession(
        hash=str(hash_),
        is_current=hash_ == current,
        is_ours=hash_ in ours or kind is not None,
        kind=cast("Literal['web'] | None", kind),
        official_app=bool(getattr(authorization, "official_app", None)),
        device_model=str(getattr(authorization, "device_model", "") or ""),
        platform=str(getattr(authorization, "platform", "") or ""),
        system_version=str(getattr(authorization, "system_version", "") or ""),
        app_name=str(getattr(authorization, "app_name", "") or ""),
        ip=str(getattr(authorization, "ip", "") or ""),
        country=str(getattr(authorization, "country", "") or ""),
        date_created=_iso(getattr(authorization, "date_created", None)),
        date_active=_iso(getattr(authorization, "date_active", None)),
        unconfirmed=bool(getattr(authorization, "unconfirmed", None)),
    )


async def _listing(client: TelegramClient) -> tuple[list[object], int | None]:
    reply = await client(GetAuthorizationsRequest())
    ttl = getattr(reply, "authorization_ttl_days", None)
    return list(getattr(reply, "authorizations", ())), ttl if isinstance(ttl, int) else None


async def _read(
    account_id: str, devices: Sequence[DeviceType], own: Mapping[str, str]
) -> AuthorizationList:
    # The deadline the session check uses: nothing below bounds itself, and an expiry is
    # the transport fault ``TimeoutError`` already means to the ladder in the caller.
    async with asyncio.timeout(settings.telegram.session_check_timeout_seconds):
        primary, *others = devices
        authorizations, ttl = await _listing(await get_client(account_id, primary))
        current = _own_hash(authorizations)
        ours = {current}
        for device in others:
            other, _ttl = await _listing(await get_client(account_id, device))
            ours.add(_own_hash(other))
    frozen_ours = frozenset(ours)
    return AuthorizationList(
        sessions=[_session(a, current=current, ours=frozen_ours, own=own) for a in authorizations],
        ttl_days=ttl,
    )


async def read_account_sessions(
    account_id: str,
    devices: Sequence[DeviceType],
    own: Mapping[str, str] | None = None,
) -> AuthorizationList:
    """Every session of ``account_id`` with ours marked; ``devices`` is primary first.

    ``own`` maps the hashes Telebuba recorded as its own authorizations (its web login) to
    their kind; they are ours although no Telethon client of the account is them.

    Asks each device's pooled client, so an account with two sign-ins answers from both.
    Raises :class:`SessionAuditGatewayError` and never a Telethon error.
    """
    try:
        return await _read(account_id, devices, own or {})
    except SessionAuditGatewayError:
        raise
    # Frozen errors subclass FloodError / BadRequestError: classify them above the flood
    # clause, as ``check_telegram_session`` does.
    except (errors.FrozenMethodInvalidError, errors.FrozenParticipantMissingError) as exc:
        raise _refusal(_FROZEN, error_type=type(exc).__name__) from exc
    except _DEAD_SESSION_ERRORS as exc:
        deactivated = _dead_session_code(exc) == "account_deactivated"
        code: SessionAuditGatewayCode = "account_deactivated" if deactivated else "session_dead"
        raise _refusal(code, error_type=type(exc).__name__) from exc
    except errors.FloodWaitError as exc:
        raise _refusal(_FLOOD, retry_after_seconds=exc.seconds) from exc
    except (TelegramClientPoolError, ConnectionError, TimeoutError) as exc:
        raise _refusal(_UNAVAILABLE, error_type=type(exc).__name__) from exc
    except errors.RPCError as exc:
        raise _refusal(_FAILED, error_type=type(exc).__name__) from exc
