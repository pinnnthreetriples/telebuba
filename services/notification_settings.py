"""Operator-bot settings — load/save the singleton row with the token masked, send a test.

The read model says whether a bot token is stored plus a short hint, like the LLM keys
in ``services.warming.settings_store``; the token itself never leaves ``services/``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.logging import log_event
from core.notify_bot import send_bot_message
from core.repositories.notification_settings import (
    load_notification_settings,
    save_notification_settings,
)
from schemas.notifications import BotMessage, NotificationSettings, NotificationTestResult
from services._notification_messages import TEST_MESSAGE

if TYPE_CHECKING:
    from schemas.notifications import (
        BotSendStatus,
        NotificationSettingsSecret,
        NotificationSettingsUpdate,
    )

# Below this length a first-and-last-four hint would reveal most of the token.
_HINT_MIN_LENGTH = 12


class NotificationsNotConfiguredError(Exception):
    """A test send was asked for with no bot token or no chat id stored."""


class NotificationDeliveryError(Exception):
    """Telegram refused the test message, or could not be reached."""

    def __init__(self, status: BotSendStatus) -> None:
        super().__init__(status)
        self.status = status


def _token_hint(token: str) -> str | None:
    if not token:
        return None
    if len(token) < _HINT_MIN_LENGTH:
        return "••••"
    return f"{token[:4]}…{token[-4:]}"


def mask_notification_settings(secret: NotificationSettingsSecret) -> NotificationSettings:
    """The response model: presence and a hint for the token, never the token."""
    return NotificationSettings(
        enabled=secret.enabled,
        has_bot_token=bool(secret.bot_token),
        bot_token_hint=_token_hint(secret.bot_token),
        chat_id=secret.chat_id,
        updated_at=secret.updated_at,
    )


async def load_settings() -> NotificationSettings:
    return mask_notification_settings(await load_notification_settings())


async def save_settings(data: NotificationSettingsUpdate) -> NotificationSettings:
    # ``clear_bot_token`` wins over a typed token; ``None`` keeps the stored one.
    token: str | None = "" if data.clear_bot_token else data.bot_token
    secret = await save_notification_settings(
        enabled=data.enabled,
        bot_token=token,
        chat_id=data.chat_id,
    )
    await log_event(
        "INFO",
        "notifications_settings_saved",
        extra={
            "enabled": secret.enabled,
            "has_bot_token": bool(secret.bot_token),
            "has_chat_id": bool(secret.chat_id),
        },
    )
    return mask_notification_settings(secret)


async def send_test_notification() -> NotificationTestResult:
    """Send the test message with the stored token and chat, whether enabled or not.

    Works while notifications are switched off on purpose: the operator checks the
    bot before turning it on.
    """
    secret = await load_notification_settings()
    if not secret.bot_token or not secret.chat_id:
        raise NotificationsNotConfiguredError
    result = await send_bot_message(
        BotMessage(bot_token=secret.bot_token, chat_id=secret.chat_id, text=TEST_MESSAGE),
    )
    if result.status != "sent":
        await log_event(
            "WARNING",
            "notifications_test_failed",
            extra={"status": result.status, "error_type": result.error_type},
        )
        raise NotificationDeliveryError(result.status)
    return NotificationTestResult()
