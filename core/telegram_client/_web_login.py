"""Mint a fresh authorization for an account so a browser can open web.telegram.org.

The problem this solves: an auth key is single-owner. Handing Python's live auth
key to a browser makes two clients share one key and Telegram answers
``AUTH_KEY_DUPLICATED``, killing both. So we never reuse the account's existing
key. Instead we run Telegram's official QR-login protocol entirely in-process to
MINT A BRAND-NEW authorization for the same account:

- the account's EXISTING pooled client plays "the phone that confirms the login";
- a FRESH throwaway :class:`telethon.TelegramClient` on an in-memory session plays
  "the new device (the browser)".

We accept the fresh client's login token with the pooled client, let the fresh
client finish the login (entering the stored 2FA password if Telegram asks),
then EXTRACT the fresh client's auth material and disconnect it permanently. The
browser becomes that authorization's sole user; Python never touches it again and
never persists its session. The pooled confirmer is left untouched — it is shared.
"""

from __future__ import annotations

import asyncio
import struct
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession
from telethon.tl import functions

from core.config import settings
from core.db import fetch_account_proxy_settings, fetch_account_twofa_password
from core.telegram_client._client import telethon_proxy_dict
from core.telegram_client._pool import get_client

if TYPE_CHECKING:
    from telethon.tl.custom import QRLogin
    from telethon.tl.types import User

    from schemas.proxy import ProxySettings

# The "new device" masquerade. The app NAME shown in Active Sessions is fixed by
# the api_id and we accept that (chasing a leaked web api_id risks
# API_ID_PUBLISHED_FLOOD); everything else is set so the entry reads like a normal
# desktop-browser Telegram Web session rather than a bot on a server.
_WEB_DEVICE_MODEL = "Chrome"
_WEB_SYSTEM_VERSION = "Windows 10"
_WEB_APP_VERSION = "4.16.8 K"
_WEB_LANG = "en"

# Telegram sends server_salt as a TL ``long`` (signed 64-bit); Telethon keeps it
# as a Python int on the connection state.
_SALT_STRUCT = struct.Struct("<q")

# QR login here never faces a human scanning a code — the confirmer accepts
# immediately in-process — so a short deadline is enough.
_QR_WAIT_TIMEOUT = 30.0

# A minted MTProto auth key is always 256 bytes; kept as a sanity assertion only.
_AUTH_KEY_BYTES = 256


class WebLoginError(Exception):
    """Minting a web authorization failed (no proxy, or a Telegram/transport fault)."""


class TwoFactorRequiredError(WebLoginError):
    """Telegram asked for the cloud password but this dashboard has none stored.

    Distinct from the parent so the caller can tell the operator to store the 2FA
    password first, rather than showing a generic failure.
    """


@dataclass(frozen=True, slots=True)
class MintedWebAuth:
    """Raw auth material for a freshly minted authorization of one account.

    The browser layer formats these into web.telegram.org localStorage; this module
    stays pure-Telegram and returns the raw bytes. ``server_salt`` is best-effort —
    ``None`` means the web client will re-negotiate it on its first request via the
    normal ``BAD_SERVER_SALT`` self-heal.
    """

    dc_id: int
    auth_key: bytes
    server_salt: bytes | None
    user_id: int


def _proxy_dict(proxy: ProxySettings) -> dict[str, object]:
    """The account's proxy as a Telethon dict, via the shared builder in ``_client``."""
    telethon = telethon_proxy_dict(
        proxy.proxy_type, proxy.host, proxy.port, proxy.username, proxy.password
    )
    if telethon is None:  # pragma: no cover - ProxySettings always has type/host/port
        msg = "proxy settings for the web session are incomplete"
        raise WebLoginError(msg)
    return telethon


def _build_new_device_client(proxy: dict[str, object]) -> TelegramClient:
    """A fresh, in-memory client for the "new device" — never persisted to disk.

    Same api_id/api_hash source as :func:`core.telegram_client.create_telegram_client`
    (never a leaked web id) and the account's own proxy, so the new session's exit IP
    matches the account.
    """
    return TelegramClient(
        StringSession(),
        settings.telegram.api_id,
        settings.telegram.api_hash,
        device_model=_WEB_DEVICE_MODEL,
        system_version=_WEB_SYSTEM_VERSION,
        app_version=_WEB_APP_VERSION,
        lang_code=_WEB_LANG,
        system_lang_code=_WEB_LANG,
        proxy=proxy,
    )


def _extract_server_salt(new_client: TelegramClient) -> bytes | None:
    """Best-effort read of the live server salt off the connection state.

    Telethon (v1.44) keeps it at ``client._sender._state.salt`` as an int, set from
    ``NewSessionCreated`` / ``BadServerSalt``. It is 0 before the first salted
    response; treat that (and any missing internal attribute) as "unknown" and let
    the web client re-negotiate.
    """
    sender = getattr(new_client, "_sender", None)
    state = getattr(sender, "_state", None)
    salt = getattr(state, "salt", 0)
    if not isinstance(salt, int) or salt == 0:
        return None
    return _SALT_STRUCT.pack(salt)


async def _finish_login(new_client: TelegramClient, qr: QRLogin, account_id: str) -> None:
    """Confirm the token with the pooled client, then complete the login, 2FA and all."""
    confirmer = await get_client(account_id)
    # Start qr.wait() FIRST so its UpdateLoginToken handler is registered before we
    # trigger the update: qr.wait() only installs that handler when entered, and the
    # server pushes updateLoginToken to the fresh client the instant we accept, so an
    # accept-then-wait order can drop the push and hang wait() until timeout.
    wait_task = asyncio.create_task(qr.wait(timeout=_QR_WAIT_TIMEOUT))
    await asyncio.sleep(0)  # let qr.wait() install its handler before we accept
    try:
        # Raw token bytes go straight across in-process — no base64 round-trip needed.
        await confirmer(functions.auth.AcceptLoginTokenRequest(token=qr.token))
        await wait_task
    except SessionPasswordNeededError as exc:
        password = await fetch_account_twofa_password(account_id)
        if not password:
            msg = "account has a cloud password but none is stored"
            raise TwoFactorRequiredError(msg) from exc
        await new_client.sign_in(password=password)
    finally:
        # If the accept failed before we awaited it, wait_task is still pending —
        # cancel and drain it so it can't outlive this call.
        if not wait_task.done():
            wait_task.cancel()
            with suppress(asyncio.CancelledError):
                await wait_task


async def _extract_minted_auth(new_client: TelegramClient, account_id: str) -> MintedWebAuth:
    """Read the completed login's raw auth material off the fresh client."""
    # get_me on an authorized client always returns a full ``User``.
    me = cast("User", await new_client.get_me())
    session = new_client.session
    if session is None or session.auth_key is None:
        # A live minted session always carries its key; absence means the login
        # never completed.
        msg = f"minted session for {account_id} is incomplete after login"
        raise WebLoginError(msg)
    auth_key = session.auth_key.key
    if len(auth_key) != _AUTH_KEY_BYTES:  # pragma: no cover - protocol invariant
        msg = f"minted auth key is {len(auth_key)} bytes, expected {_AUTH_KEY_BYTES}"
        raise WebLoginError(msg)
    return MintedWebAuth(
        dc_id=session.dc_id,
        auth_key=auth_key,
        server_salt=_extract_server_salt(new_client),
        user_id=me.id,
    )


async def mint_web_authorization(account_id: str) -> MintedWebAuth:
    """Mint a fresh authorization for ``account_id`` and return its raw auth material.

    The pooled confirmer client is borrowed, never disconnected. The fresh "new
    device" client is always disconnected and its session never written anywhere.
    """
    proxy = await fetch_account_proxy_settings(account_id)
    if proxy is None:
        # The button is disabled without a proxy, but guard anyway: a keyless new
        # session must not go out on the host IP and mismatch the account.
        msg = f"account {account_id} has no proxy; refusing to mint a web session"
        raise WebLoginError(msg)

    new_client = _build_new_device_client(_proxy_dict(proxy))
    try:
        await new_client.connect()
        qr = await new_client.qr_login()
        await _finish_login(new_client, qr, account_id)
        return await _extract_minted_auth(new_client, account_id)
    except WebLoginError:
        raise
    except Exception as exc:  # any Telethon/transport fault becomes our own type
        msg = f"failed to mint a web authorization for {account_id}: {exc}"
        raise WebLoginError(msg) from exc
    finally:
        # Never reuse this key in Python and never persist the session — the browser
        # is its sole owner from here on.
        await new_client.disconnect()
