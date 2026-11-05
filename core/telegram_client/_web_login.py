"""Accept a browser's Telegram Web QR login with the account's own session.

WebK (web.telegram.org/k/) shows its QR login and calls ``auth.exportLoginToken``;
a document-start hook in the browser captures that token. This module hands the raw
token to the account's EXISTING pooled client via ``auth.acceptLoginToken``, so WebK
completes its OWN login through the account we already control — no separate
authorization is minted and nothing is persisted. The token WebK exports rotates,
so the caller retries with the next captured token when an accept is rejected.
"""

from __future__ import annotations

from telethon.errors import (
    AuthTokenAlreadyAcceptedError,
    AuthTokenExceptionError,
    AuthTokenExpiredError,
    AuthTokenInvalid2Error,
    AuthTokenInvalidError,
    AuthTokenInvalidxError,
    FloodWaitError,
    ServerError,
    TimedOutError,
)
from telethon.tl import functions

from core.telegram_client._pool import get_client

# WebK's exported token rotates; an accept against a stale one raises one of these.
# Kept here (not in services) so the telethon dependency stays inside core.
_ROTATION_ERRORS = (
    AuthTokenExpiredError,
    AuthTokenAlreadyAcceptedError,
    AuthTokenInvalidxError,
    AuthTokenInvalidError,
    AuthTokenInvalid2Error,
    AuthTokenExceptionError,
)
# A hiccup of the pooled connection, not a verdict on the token: the very same token is
# worth offering again. Telethon already sleeps through FLOOD_WAITs up to its own
# threshold, so one reaching us is longer than that — retried only while it is short
# enough to fit inside the drive's deadline.
_TRANSIENT_ERRORS = (ConnectionError, TimeoutError, OSError, ServerError, TimedOutError)
_MAX_FLOOD_WAIT_SECONDS = 15
# A locked, missing or unreadable session file surfaces as an OSError subclass, but no retry
# repairs it: it is a real failure of the account, not a hiccup of the connection.
_FILE_ERRORS = (FileNotFoundError, PermissionError, IsADirectoryError)


class WebLoginTransientError(Exception):
    """The accept did not get a verdict (connection, timeout, short flood wait): retry it.

    ``retry_after`` is how long to hold off, in seconds, when Telegram said so.
    """

    def __init__(self, retry_after: float = 0.0) -> None:
        super().__init__("transient failure accepting the web login token")
        self.retry_after = retry_after


async def accept_web_login_token(account_id: str, token: bytes) -> str | None:
    """Accept ``token`` (WebK's exported login token) with the account's pooled client.

    Returns the hash of the authorization Telegram created for the browser when the token was
    accepted (``""`` if the answer carried none), ``None`` when it had already rotated
    (expired / already-accepted / invalidated) so the caller retries the next captured
    token. A connection fault, timeout or short flood wait raises
    :class:`WebLoginTransientError` so the caller retries the SAME token within its own
    deadline. A file-system error (missing, locked or unreadable session file) and any other
    Telethon error propagate as a genuine failure.
    """
    try:
        confirmer = await get_client(account_id)
        accepted = await confirmer(functions.auth.AcceptLoginTokenRequest(token=token))
    except _ROTATION_ERRORS:
        return None
    except _FILE_ERRORS:
        raise
    except FloodWaitError as exc:
        if exc.seconds > _MAX_FLOOD_WAIT_SECONDS:
            raise
        raise WebLoginTransientError(float(exc.seconds)) from exc
    except _TRANSIENT_ERRORS as exc:
        raise WebLoginTransientError from exc
    new_hash = getattr(accepted, "hash", None)
    return str(new_hash) if isinstance(new_hash, int) and not isinstance(new_hash, bool) else ""
