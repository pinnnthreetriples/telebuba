"""Pool rebuild hooks — split out of ``_pool`` for the file-size budget."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.logging import log_event

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from telethon import TelegramClient

    _RebuildHook = Callable[[str, TelegramClient], Awaitable[None]]

# Stdlib sink for full third-party text — see ``core.proxy_check._failed_result``.
logger = logging.getLogger(__name__)

# Callbacks invoked after a fresh client is built for an account, so standing
# subscriptions (the post listener) can re-register their handlers on the new
# connection. ``_listener`` sets this at import time — the dependency points
# core→core, never into services.
_REBUILD_HOOKS: list[_RebuildHook] = []


def register_rebuild_hook(hook: _RebuildHook) -> None:
    """Register a callback fired with ``(account_id, client)`` after a rebuild.

    Idempotent per callable so a re-import can't stack duplicate hooks. Used by
    ``_listener`` to re-attach its ``NewMessage`` handler when the pool replaces
    a dropped connection.
    """
    if hook not in _REBUILD_HOOKS:
        _REBUILD_HOOKS.append(hook)


async def fire_rebuild_hooks(account_id: str, client: TelegramClient) -> None:
    """Let standing subscriptions re-register on a freshly built client.

    Runs outside the connect lock so a hook can safely re-enter the pool. A
    hook fault is logged and swallowed — a listener that can't re-attach must
    not break the borrower that triggered the rebuild.
    """
    for hook in _REBUILD_HOOKS:
        try:
            await hook(account_id, client)
        except Exception as exc:  # a hook fault must not break get_client.
            logger.exception("pool rebuild hook failed for %s", account_id)
            await log_event(
                "WARNING",
                "telegram_pool_rebuild_hook_failed",
                account_id=account_id,
                extra={"error_type": type(exc).__name__},
            )
