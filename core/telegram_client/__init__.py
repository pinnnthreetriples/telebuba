"""Telegram gateway — the only place Telethon is constructed and called.

The public API is re-exported here so callers keep importing from
``core.telegram_client``; the implementation is split across private submodules
to keep each file small:

- ``_client``  — client construction + per-call lifecycle (probe paths only)
- ``_pool``    — long-lived connected-client cache, one per account
- ``_session`` — session liveness check
- ``_spam``    — @SpamBot probe + self-restriction read
- ``_actions`` — typed-action executor + dispatch (uses the pool)
- ``_read``    — read-action executor + batch dispatch (uses the pool)
- ``_read_chat`` — chat resolve + read-by-id (the per-account chat id lives here)
- ``_read_rights`` — the write-rights read, split out of ``_read`` for its size
- ``_copy_media`` — media copy (never a forward) with one stale-reference retry
- ``_read_post_image`` — in-memory fetch of a channel post's photo (vision path)
- ``_listener``— standing NewMessage subscription → typed NewPostEvent callback
- ``_media``   — profile photo / story / music actions
- ``_profile`` — profile-field edit dispatch + edit-time status bookkeeping
- ``_privacy`` — account-privacy key read/write dispatch (avatar/bio visibility)

Tests that monkeypatch internals target the submodule that owns the name
(e.g. ``core.telegram_client._actions.get_client``), not this namespace.
"""

from __future__ import annotations

from core.telegram_client._action_results import UNCONFIRMED_ERROR_TYPE
from core.telegram_client._actions import execute
from core.telegram_client._auth import (
    log_out_session,
    remove_account_session,
    request_phone_code,
    submit_phone_code,
)
from core.telegram_client._client import (
    create_telegram_client,
    prepare_session_check_profile,
    prepare_telegram_client_profile,
    telegram_client,
)
from core.telegram_client._listener import (
    fetch_recent_posts,
    forget_post_listener,
    stop_post_listener,
    subscribe_posts,
    take_lost_access_channels,
    update_post_subscription,
)
from core.telegram_client._media import refresh_account_avatar
from core.telegram_client._pool import (
    TelegramClientPoolError,
    evict_client,
    get_client,
    removing_client,
    shutdown_telegram_pool,
)
from core.telegram_client._react import invalidate_reaction_whitelist_cache
from core.telegram_client._read import (
    TelegramAccountNotFoundError,
    TelegramReadError,
    execute_read,
    execute_read_many,
)
from core.telegram_client._read_post_image import download_post_image
from core.telegram_client._session import check_telegram_session
from core.telegram_client._spam import check_spam_status
from core.telegram_client._web_login import (
    MintedWebAuth,
    TwoFactorRequiredError,
    WebLoginError,
    mint_web_authorization,
)

__all__ = [
    "UNCONFIRMED_ERROR_TYPE",
    "MintedWebAuth",
    "TelegramAccountNotFoundError",
    "TelegramClientPoolError",
    "TelegramReadError",
    "TwoFactorRequiredError",
    "WebLoginError",
    "check_spam_status",
    "check_telegram_session",
    "create_telegram_client",
    "download_post_image",
    "evict_client",
    "execute",
    "execute_read",
    "execute_read_many",
    "fetch_recent_posts",
    "forget_post_listener",
    "get_client",
    "invalidate_reaction_whitelist_cache",
    "log_out_session",
    "mint_web_authorization",
    "prepare_session_check_profile",
    "prepare_telegram_client_profile",
    "refresh_account_avatar",
    "remove_account_session",
    "removing_client",
    "request_phone_code",
    "shutdown_telegram_pool",
    "stop_post_listener",
    "submit_phone_code",
    "subscribe_posts",
    "take_lost_access_channels",
    "telegram_client",
    "update_post_subscription",
]
