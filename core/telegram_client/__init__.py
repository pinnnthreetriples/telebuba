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
- ``_listener``— standing NewMessage subscription → typed NewPostEvent callback
- ``_media``   — profile photo / story / music actions

Tests that monkeypatch internals target the submodule that owns the name
(e.g. ``core.telegram_client._actions.get_client``), not this namespace.
"""

from __future__ import annotations

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
    stop_post_listener,
    subscribe_posts,
    update_post_subscription,
)
from core.telegram_client._media import refresh_account_avatar
from core.telegram_client._pool import (
    TelegramClientPoolError,
    evict_client,
    get_client,
    shutdown_telegram_pool,
)
from core.telegram_client._read import (
    TelegramAccountNotFoundError,
    TelegramReadError,
    execute_read,
    execute_read_many,
)
from core.telegram_client._session import check_telegram_session
from core.telegram_client._spam import check_spam_status

__all__ = [
    "TelegramAccountNotFoundError",
    "TelegramClientPoolError",
    "TelegramReadError",
    "check_spam_status",
    "check_telegram_session",
    "create_telegram_client",
    "evict_client",
    "execute",
    "execute_read",
    "execute_read_many",
    "get_client",
    "log_out_session",
    "prepare_session_check_profile",
    "prepare_telegram_client_profile",
    "refresh_account_avatar",
    "remove_account_session",
    "request_phone_code",
    "shutdown_telegram_pool",
    "stop_post_listener",
    "submit_phone_code",
    "subscribe_posts",
    "telegram_client",
    "update_post_subscription",
]
