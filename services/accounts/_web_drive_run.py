"""Driving a first-open web login under a hard deadline.

Split out of ``web_login`` for the file-size budget; the deadline is passed in so the
caller owns (and tests can shrink) it.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.logging import log_event
from schemas.accounts import OpenWebResult
from services.accounts._web_drive import DriveTrace, drive_login, mark_signed_in
from services.accounts._web_errors import _DRIVE_REFUSAL, WebLoginLaunchError

if TYPE_CHECKING:
    from pathlib import Path

    from core.web_login.browser import WebWindow

# Held here: a bare ``create_task`` can be garbage-collected mid-run.
_recorders: set[asyncio.Task[None]] = set()


async def drive_and_mark(
    account_id: str,
    window: WebWindow,
    profile: Path,
    *,
    timeout: float,  # noqa: ASYNC109
) -> OpenWebResult:
    """Drive the login under a hard deadline and mark the profile only on success.

    The deadline is a ``wait_for``, not a loop-entry check: each CDP command has its
    own 30 s timeout and typing an N-character password issues 2N+8 of them, so a
    wedged-but-connected renderer could otherwise hold the HTTP request for minutes.
    A timeout is "did not sign in", never an error — the window is up and the operator
    can finish by hand, and the profile stays unmarked so the next click drives again.

    Every outcome is logged as one ``web_login_*`` event with non-secret fields only.
    ``trace.started_at`` is the wall-clock start of the drive: on success it is the lower
    bound for "authorizations created by this web login".
    """
    trace = DriveTrace()
    try:
        signed_in = await asyncio.wait_for(
            drive_login(account_id, window, trace=trace),
            timeout,
        )
    except TimeoutError:
        signed_in = False
    except Exception as exc:
        await _log_outcome(account_id, trace, "web_login_refused", error=type(exc).__name__)
        # Blind on purpose: the callees raise anything. ``token_bytes`` raises
        # binascii.Error on a malformed capture, and ``accept_web_login_token`` raises
        # Telethon errors for a dead or deauthorized session — exactly the account an
        # operator wants to inspect. Uncaught, those are bare 500s with a traceback.
        raise WebLoginLaunchError(_DRIVE_REFUSAL) from exc
    if not signed_in:
        await _log_outcome(account_id, trace, trace.failure_code())
        return OpenWebResult(launched=True, signed_in=False, reason=trace.classify_failure())
    mark_signed_in(profile)
    await _log_outcome(account_id, trace, "web_login_success")
    _record_in_background(account_id, window, trace)
    return OpenWebResult(launched=True, signed_in=True)


async def _log_outcome(
    account_id: str, trace: DriveTrace, event: str, *, error: str | None = None
) -> None:
    """One structured line per drive. Counts and states only: never a token or a password."""
    extra: dict[str, object] = {
        "state": trace.state,
        "typed_password": trace.typed_password,
        "has_stored_password": trace.has_stored_password,
        "password_attempts": trace.password_attempts,
        "tokens_seen": trace.tokens_seen,
        "tokens_accepted": trace.tokens_accepted,
        "transient_retries": trace.transient_retries,
        "cdp_errors": trace.cdp_errors,
        "elapsed_s": round(trace.elapsed, 1),
        "started_at": trace.started_at.isoformat(),
    }
    if error is not None:
        extra["error"] = error
    level = "INFO" if event == "web_login_success" else "WARNING"
    await log_event(level, event, account_id=account_id, extra=extra)


def _record_in_background(account_id: str, window: WebWindow, trace: DriveTrace) -> None:
    """Record this login's authorization as ours without ever delaying or failing the login."""
    seen_at = trace.logged_in_at or datetime.now(UTC)
    task = asyncio.create_task(_record_own(account_id, window, trace.started_at, seen_at))
    _recorders.add(task)
    task.add_done_callback(_recorders.discard)


async def _record_own(
    account_id: str, window: WebWindow, since: datetime, seen_at: datetime
) -> None:
    try:
        await _record_own_authorizations(account_id, window, since, seen_at)
    except Exception as exc:  # noqa: BLE001
        # Blind on purpose: the login already succeeded and nothing here may undo it. The
        # worst case is the audit showing our own web login as foreign until the next audit.
        await log_event(
            "WARNING",
            "own_web_authorizations_unrecorded",
            account_id=account_id,
            extra={"error_type": type(exc).__name__},
        )


async def _record_own_authorizations(
    account_id: str, window: WebWindow, since: datetime, seen_at: datetime
) -> int:
    from services.accounts.own_authorizations import (  # noqa: PLC0415 - import cycle
        record_own_web_login,
    )

    return await record_own_web_login(account_id, window, since=since, seen_at=seen_at)


async def take_login_baseline(account_id: str, window: WebWindow) -> None:
    """Keep the account's authorization hashes with a window launched for an unsigned profile.

    Best effort and never a failure of the launch: without a baseline the recording falls back
    to its bounded time window.
    """
    from services.accounts.own_authorizations import (  # noqa: PLC0415 - import cycle
        remember_login_baseline,
    )

    try:
        await remember_login_baseline(account_id, window)
    except Exception as exc:  # noqa: BLE001 - the login must go on without it
        await log_event(
            "WARNING",
            "own_web_baseline_failed",
            account_id=account_id,
            extra={"error_type": type(exc).__name__},
        )


async def cancel_recorders() -> None:
    """Cancel the recordings still running (shutdown): none may outlive the process's loop."""
    tasks = list(_recorders)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
