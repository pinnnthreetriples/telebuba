"""Accept a browser's Telegram Web QR login with the account's own session.

WebK (web.telegram.org/k/) shows its QR login and calls ``auth.exportLoginToken``;
a document-start hook in the browser captures that token. This module hands the raw
token to the account's EXISTING pooled client via ``auth.acceptLoginToken``, so WebK
completes its OWN login through the account we already control — no separate
authorization is minted and nothing is persisted. The token WebK exports rotates,
so the caller retries with the next captured token when an accept is rejected.
"""

from __future__ import annotations

from telethon.tl import functions

from core.telegram_client._pool import get_client


class WebLoginError(Exception):
    """A web-login guard failed (no proxy, or the browser/relay could not start)."""


async def accept_web_login_token(account_id: str, token: bytes) -> None:
    """Accept ``token`` (WebK's exported login token) with the account's pooled client.

    Telethon's ``AUTH_TOKEN_EXPIRED`` / ``AUTH_TOKEN_ALREADY_ACCEPTED`` /
    ``AUTH_TOKEN_INVALIDX`` errors are left to propagate: the exported token rotates,
    so the caller treats those as "try the next captured token".
    """
    confirmer = await get_client(account_id)
    await confirmer(functions.auth.AcceptLoginTokenRequest(token=token))
