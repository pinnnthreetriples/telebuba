"""Layer-legal adapter from Telegram updates to the transient inbox event bus."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.inbox_events import publish
from core.logging import log_event
from core.telegram_client import account_auth_lock

if TYPE_CHECKING:
    from schemas.inbox_events import InboxMessageReceived

logger = logging.getLogger(__name__)


async def _journal_service_message(event: InboxMessageReceived) -> None:
    """Journal a message from Telegram's service user; never raises, never touches the text.

    A login code Telebuba did not ask for is somebody else signing in to the account. Any
    other service message is only noted. The message text is not here: the gateway reduced it
    to ``service_kind``, so the code cannot reach a log.
    """
    try:
        # Lazy: services.accounts imports the inbox runtime, which imports this module.
        from services.accounts._expected_codes import is_login_code_expected  # noqa: PLC0415

        if event.service_kind == "login_code":
            if (
                is_login_code_expected(event.account_id)
                or account_auth_lock(event.account_id).locked()
            ):
                return
            await log_event("WARNING", "login_code_unrequested", account_id=event.account_id)
        elif event.service_kind == "other":
            await log_event("INFO", "telegram_service_message", account_id=event.account_id)
    except Exception as exc:  # noqa: BLE001 - the journal must never cost the inbox a message
        logger.warning("service message journal failed (%s)", type(exc).__name__)


async def on_message_received(event: InboxMessageReceived) -> None:
    """Publish a typed message nudge; message content is fetched through HTTP."""
    publish(event)
    if event.service_kind is not None:
        await _journal_service_message(event)
