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
    AuthTokenExpiredError,
    AuthTokenInvalidxError,
)
from telethon.tl import functions

from core.telegram_client._pool import get_client

# WebK's exported token rotates; an accept against a stale one raises one of these.
# Kept here (not in services) so the telethon dependency stays inside core.
_ROTATION_ERRORS = (
    AuthTokenExpiredError,
    AuthTokenAlreadyAcceptedError,
    AuthTokenInvalidxError,
)


class WebLoginError(Exception):
    """A web-login guard failed (no proxy, or the browser/relay could not start)."""


async def accept_web_login_token(account_id: str, token: bytes) -> bool:
    """Accept ``token`` (WebK's exported login token) with the account's pooled client.

    Returns ``True`` when the token was accepted, ``False`` when it had already rotated
    (expired / already-accepted / invalidated) so the caller retries the next captured
    token. Any other Telethon error propagates as a genuine failure.
    """
    confirmer = await get_client(account_id)
    try:
        await confirmer(functions.auth.AcceptLoginTokenRequest(token=token))
    except _ROTATION_ERRORS:
        return False
    return True
