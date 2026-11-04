"""QR-code sign-in — the only place ``auth.exportLoginToken`` lives.

The operator scans a QR code from a Telegram app that is already signed in to the
account (Settings -> Devices -> Link Desktop Device); Telegram then authorizes THIS
client's session. ``start_qr_login`` builds the client, ``read_qr_login`` reports
where it stands, ``submit_qr_password`` answers a 2FA prompt and ``cancel_qr_login``
drops it.

One background task owns the whole sign-in, and with it the client and the account's
auth lock, for as long as the connection lives (the reasoning is ``_auth``'s module
docstring: two Telethon handles on one ``.session`` file corrupt each other, and the
phone-code, logout and second-device flows take the same lock). That is also why the
sign-in has a deadline (``qr_login_timeout_seconds``): a QR shown and forgotten must
not hold the account's lock forever.

The client is built with updates ON, unlike the other auth flows. Telethon's
``QRLogin.wait`` completes when Telegram pushes ``UpdateLoginToken`` after the scan,
and a client without updates never hears it. A token lives about 30 seconds, so the
task re-exports a fresh one each time it lapses and publishes the new image.

State is in memory (one uvicorn worker), like ``services.accounts._login_state``.
The login token and its ``tg://`` URL are never logged or stored: the rendered image
is the only place they surface, and failures are classified by exception class name
(a transport error stringifies with the proxy ``user:pass@host:port``).
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from telethon import errors

from core.config import settings
from core.logging import log_event
from core.qr_image import render_qr_data_uri
from core.telegram_client._auth import _alive_result, _auth_lock
from core.telegram_client._client import create_telegram_client, prepare_telegram_client_profile
from core.telegram_client._pool import removing_client
from schemas.device_fingerprint import TelegramClientRequest
from schemas.qr_login import QrLoginState

if TYPE_CHECKING:
    from collections.abc import Callable

    from telethon import TelegramClient

    from schemas.device_fingerprint import TelegramClientProfile
    from schemas.qr_login import QrLoginErrorCode, QrLoginRequest

__all__ = [
    "QrLoginGatewayError",
    "cancel_qr_login",
    "forget_qr_login",
    "read_qr_login",
    "shutdown_qr_logins",
    "start_qr_login",
    "submit_qr_password",
]


class QrLoginGatewayError(ValueError):
    """A QR-login call the current state cannot take; ``code`` is the stable reason."""

    def __init__(self, code: QrLoginErrorCode) -> None:
        super().__init__(code)
        self.code: QrLoginErrorCode = code


@dataclass
class _QrSession:
    """One in-flight QR sign-in: its task, its latest state and the 2FA inbox."""

    state: QrLoginState
    deadline: float
    task: asyncio.Task[None] | None = None
    # Notified on every publish; ``version`` lets a waiter tell "something new".
    changed: asyncio.Condition = field(default_factory=asyncio.Condition)
    version: int = 0
    passwords: asyncio.Queue[str] = field(default_factory=asyncio.Queue)


_SESSIONS: dict[str, _QrSession] = {}
_NO_QR_LOGIN: QrLoginErrorCode = "no_qr_login"
_NOT_AWAITING: QrLoginErrorCode = "not_awaiting_password"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


async def _publish(session: _QrSession, **fields: object) -> None:
    """Replace the session's state and wake every waiter."""
    account_id = session.state.account_id
    async with session.changed:
        session.state = QrLoginState.model_validate({"account_id": account_id, **fields})
        session.version += 1
        session.changed.notify_all()


async def _wait_until(
    session: _QrSession,
    ready: Callable[[_QrSession], bool],
    *,
    timeout: float,  # noqa: ASYNC109 - a deadline on a condition, not a cancel scope
) -> None:
    async def _until() -> None:
        async with session.changed:
            await session.changed.wait_for(lambda: ready(session))

    await asyncio.wait_for(_until(), timeout)


def read_qr_login(account_id: str) -> QrLoginState | None:
    """The account's current QR sign-in state, or ``None`` when none is open."""
    session = _SESSIONS.get(account_id)
    return session.state if session is not None else None


async def start_qr_login(request: QrLoginRequest) -> QrLoginState:
    """Open a QR sign-in (replacing any earlier one) and return once a QR is ready.

    Lets ``DeviceUnavailableError`` from the profile lookup propagate; the service
    maps it. Returns a ``failed`` state when nothing happened in time.
    """
    account_id = request.account_id
    await cancel_qr_login(account_id)
    profile = await prepare_telegram_client_profile(
        TelegramClientRequest(
            account_id=account_id,
            session_name=request.session_name,
            receive_updates=True,
        ),
    )
    session = _QrSession(
        state=QrLoginState(account_id=account_id, status="waiting"),
        deadline=asyncio.get_running_loop().time() + settings.telegram.qr_login_timeout_seconds,
    )
    _SESSIONS[account_id] = session
    session.task = asyncio.create_task(
        _run(session, request, profile),
        name=f"qr-login-{account_id}",
    )
    try:
        # Any publish at all: the first QR, or the failure/expiry that replaced it.
        await _wait_until(
            session,
            lambda s: s.version > 0,
            timeout=settings.telegram.timeout_seconds,
        )
    except TimeoutError:
        await cancel_qr_login(account_id)
        return QrLoginState(account_id=account_id, status="failed", error_code="connection_failed")
    return session.state


async def submit_qr_password(account_id: str, password: str) -> QrLoginState:
    """Hand the 2FA password to the open sign-in and return its next state."""
    session = _SESSIONS.get(account_id)
    if session is None:
        raise QrLoginGatewayError(_NO_QR_LOGIN)
    if session.state.status != "password_needed":
        raise QrLoginGatewayError(_NOT_AWAITING)
    seen = session.version
    session.passwords.put_nowait(password)
    try:
        await _wait_until(
            session,
            lambda s: s.version > seen,
            timeout=settings.telegram.timeout_seconds,
        )
    except TimeoutError:
        return QrLoginState(account_id=account_id, status="failed", error_code="connection_failed")
    return session.state


async def cancel_qr_login(account_id: str) -> None:
    """Stop the account's QR sign-in, releasing its client and auth lock; no-op if none."""
    session = _SESSIONS.pop(account_id, None)
    if session is None or session.task is None:
        return
    session.task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await session.task


def forget_qr_login(account_id: str) -> None:
    """Drop a FINISHED sign-in's entry once the service has persisted its outcome."""
    _SESSIONS.pop(account_id, None)


async def shutdown_qr_logins() -> None:
    """Cancel every open QR sign-in (app shutdown)."""
    for account_id in list(_SESSIONS):
        await cancel_qr_login(account_id)


async def _run(
    session: _QrSession,
    request: QrLoginRequest,
    profile: TelegramClientProfile,
) -> None:
    """The sign-in itself; every terminal publish happens while the lock is held."""
    async with (
        _auth_lock(request.account_id),
        removing_client(request.account_id, profile.device_type),
    ):
        client = create_telegram_client(profile)
        try:
            await client.connect()
            await _sign_in(session, client, request, profile)
        except errors.FloodWaitError as exc:
            await _publish(
                session,
                status="failed",
                error_code="flood_wait",
                flood_wait_seconds=exc.seconds,
            )
        except Exception as exc:  # noqa: BLE001 - classify any SDK/network failure for the UI.
            # Class name only, never ``str(exc)``: a proxy error stringifies with the
            # proxy ``user:pass@host:port``.
            await log_event(
                "WARNING",
                "qr_login_failed",
                account_id=request.account_id,
                extra={"error_type": type(exc).__name__},
            )
            await _publish(session, status="failed", error_code="connection_failed")
        finally:
            await client.disconnect()


async def _sign_in(
    session: _QrSession,
    client: TelegramClient,
    request: QrLoginRequest,
    profile: TelegramClientProfile,
) -> None:
    if await client.is_user_authorized():
        # The session was already signed in (a retry after a lost response).
        await _publish(
            session,
            status="alive",
            result=_alive_result(profile, await client.get_me()),
        )
        return
    qr = await client.qr_login(ignored_ids=request.ignored_user_ids)
    loop = asyncio.get_running_loop()
    while True:
        remaining_total = session.deadline - loop.time()
        if remaining_total <= 0:
            await _publish(session, status="expired")
            return
        expires_at = _aware(qr.expires)
        await _publish(
            session,
            status="waiting",
            qr_image=render_qr_data_uri(qr.url),
            expires_at=expires_at,
        )
        remaining_token = (expires_at - _utcnow()).total_seconds()
        try:
            await qr.wait(timeout=max(1.0, min(remaining_token, remaining_total)))
        except TimeoutError:
            if session.deadline - loop.time() <= 0:
                await _publish(session, status="expired")
                return
            await qr.recreate()
            continue
        except errors.SessionPasswordNeededError:
            if not await _await_password(session, client):
                return
        break
    await _publish(
        session,
        status="alive",
        result=_alive_result(profile, await client.get_me()),
    )


async def _await_password(session: _QrSession, client: TelegramClient) -> bool:
    """Ask for the cloud password until it is right; ``False`` when the deadline ends it."""
    loop = asyncio.get_running_loop()
    await _publish(session, status="password_needed")
    while True:
        remaining = session.deadline - loop.time()
        try:
            password = await asyncio.wait_for(session.passwords.get(), max(remaining, 0))
        except TimeoutError:
            await _publish(session, status="expired")
            return False
        try:
            await client.sign_in(password=password)
        except errors.PasswordHashInvalidError:
            await _publish(session, status="password_needed", error_code="password_invalid")
            continue
        return True
