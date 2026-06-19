"""Telegram gateway — the only place Telethon is constructed and called.

The public API is re-exported here so callers keep importing from
``core.telegram_client``; the implementation is split across private submodules
to keep each file small:

- ``_client``  — client construction + lifecycle
- ``_session`` — session liveness check
- ``_spam``    — @SpamBot probe + self-restriction read
- ``_actions`` — typed-action executor + dispatch
- ``_media``   — profile photo / story / music actions

Tests that monkeypatch internals target the submodule that owns the name
(e.g. ``core.telegram_client._actions.telegram_client``), not this namespace.
"""

from __future__ import annotations

from core.telegram_client._actions import execute
from core.telegram_client._client import (
    create_telegram_client,
    prepare_session_check_profile,
    prepare_telegram_client_profile,
    telegram_client,
)
from core.telegram_client._read import (
    TelegramAccountNotFoundError,
    TelegramReadError,
    execute_read,
)
from core.telegram_client._session import check_telegram_session
from core.telegram_client._spam import check_spam_status

__all__ = [
    "TelegramAccountNotFoundError",
    "TelegramReadError",
    "check_spam_status",
    "check_telegram_session",
    "create_telegram_client",
    "execute",
    "execute_read",
    "prepare_session_check_profile",
    "prepare_telegram_client_profile",
    "telegram_client",
]
