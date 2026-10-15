"""Operator-bot notifications — settings contracts and the gateway's send verdict.

The bot token is a secret: only :class:`NotificationSettingsSecret` carries it, and it
never leaves ``services/``. The read model the API returns says whether a token is
stored plus a short hint, the same way the LLM keys are masked.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Telegram bot tokens are ``<bot id>:<secret>``. The token is spliced into the Bot API
# URL path, so anything outside this alphabet (a ``/``, ``?`` or ``#``) is refused
# before it can reshape the request.
BOT_CREDENTIAL_SHAPE = r"^\d{1,20}:[A-Za-z0-9_-]{20,100}$"
# A numeric user/group/channel id, or a public ``@channel`` username.
CHAT_ID_PATTERN = r"^(-?\d{1,20}|@[A-Za-z][A-Za-z0-9_]{3,31})$"

NotificationKind = Literal[
    "quarantine", "fleet_dm_paused", "spam_limited", "spam_blocked", "error", "warmed"
]
BotSendStatus = Literal["sent", "rejected", "unreachable"]


class NotificationSettingsSecret(BaseModel):
    """The stored row, token included — services only."""

    enabled: bool = False
    bot_token: str = Field(default="", repr=False)
    chat_id: str = ""
    updated_at: str | None = None


class NotificationSettings(BaseModel):
    """What the Settings page reads: the token only as presence plus a hint."""

    enabled: bool
    has_bot_token: bool
    bot_token_hint: str | None = None
    chat_id: str
    updated_at: str | None = None


class NotificationSettingsUpdate(BaseModel):
    """Keep/clear/replace for the token, like the LLM keys; ``None`` keeps a field."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    bot_token: str | None = Field(default=None, pattern=BOT_CREDENTIAL_SHAPE, repr=False)
    clear_bot_token: bool = False
    # "" clears the chat id; ``None`` keeps it.
    chat_id: str | None = Field(default=None, pattern=rf"{CHAT_ID_PATTERN}|^$")


class BotMessage(BaseModel):
    """One outbound ``sendMessage`` call."""

    bot_token: str = Field(min_length=1, repr=False)
    chat_id: str = Field(min_length=1)
    text: str = Field(min_length=1, max_length=4096)


class BotSendResult(BaseModel):
    """The gateway's verdict — it never raises for a delivery failure.

    ``rejected`` is Telegram answering no (bad token, unknown chat, bot blocked);
    ``unreachable`` is the network or a timeout. ``error_type`` is an exception class
    name or the Bot API's HTTP status, never text that could quote the token.
    """

    status: BotSendStatus
    error_type: str | None = None


class NotificationTestResult(BaseModel):
    """``POST /notifications/test`` succeeded: the message was delivered."""

    sent: bool = True
