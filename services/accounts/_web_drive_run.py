"""Driving a first-open web login under a hard deadline.

Split out of ``web_login`` for the file-size budget; the deadline is passed in so the
caller owns (and tests can shrink) it.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from schemas.accounts import OpenWebResult
from services.accounts._web_drive import DriveTrace, drive_login, mark_signed_in
from services.accounts._web_errors import _DRIVE_REFUSAL, WebLoginLaunchError

if TYPE_CHECKING:
    from pathlib import Path

    from core.web_login.browser import WebWindow


async def drive_and_mark(
    account_id: str,
    window: WebWindow,
    profile: Path,
    *,
    type_password: bool,
    timeout: float,  # noqa: ASYNC109
) -> OpenWebResult:
    """Drive the login under a hard deadline and mark the profile only on success.

    The deadline is a ``wait_for``, not a loop-entry check: each CDP command has its
    own 30 s timeout and typing an N-character password issues 2N+8 of them, so a
    wedged-but-connected renderer could otherwise hold the HTTP request for minutes.
    A timeout is "did not sign in", never an error — the window is up and the operator
    can finish by hand, and the profile stays unmarked so the next click drives again.
    """
    trace = DriveTrace()
    try:
        signed_in = await asyncio.wait_for(
            drive_login(account_id, window, type_password=type_password, trace=trace),
            timeout,
        )
    except TimeoutError:
        signed_in = False
    except Exception as exc:
        # Blind on purpose: the callees raise anything. ``token_bytes`` raises
        # binascii.Error on a malformed capture, and ``accept_web_login_token`` raises
        # Telethon errors for a dead or deauthorized session — exactly the account an
        # operator wants to inspect. Uncaught, those are bare 500s with a traceback.
        raise WebLoginLaunchError(_DRIVE_REFUSAL) from exc
    if not signed_in:
        return OpenWebResult(launched=True, signed_in=False, reason=trace.classify_failure())
    mark_signed_in(profile)
    return OpenWebResult(launched=True, signed_in=True)
