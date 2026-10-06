"""Refusals of the chat-broadcast domain: stable codes and the classes that carry them.

``api/`` maps the class to the HTTP status; the SPA words the code (``shell.code.*``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from schemas.chat_broadcast import ChatBroadcastRefusalCode

CAMPAIGN_RUNNING: ChatBroadcastRefusalCode = "campaign_running"
CAMPAIGN_CHANGED: ChatBroadcastRefusalCode = "campaign_changed"
NO_ACCOUNTS: ChatBroadcastRefusalCode = "no_accounts"
NO_FREE_ACCOUNTS: ChatBroadcastRefusalCode = "no_free_accounts"
NO_TARGETS: ChatBroadcastRefusalCode = "no_targets"
NO_MESSAGES: ChatBroadcastRefusalCode = "no_messages"
INVALID_TARGET: ChatBroadcastRefusalCode = "invalid_target"
INVALID_POST_LINK: ChatBroadcastRefusalCode = "invalid_post_link"
TOO_MANY_TARGETS: ChatBroadcastRefusalCode = "too_many_targets"
TOO_MANY_ACCOUNTS: ChatBroadcastRefusalCode = "too_many_accounts"
ACCOUNT_NOT_FOUND: ChatBroadcastRefusalCode = "account_not_found"
TARGET_NOT_FOUND: ChatBroadcastRefusalCode = "target_not_found"
TARGET_STATE_CHANGED: ChatBroadcastRefusalCode = "target_state_changed"
ACCOUNT_NOT_IN_CAMPAIGN: ChatBroadcastRefusalCode = "account_not_in_campaign"
MEDIA_INVALID: ChatBroadcastRefusalCode = "media_invalid"
MEDIA_TOO_LARGE: ChatBroadcastRefusalCode = "media_too_large"
CAPTION_TOO_LONG: ChatBroadcastRefusalCode = "caption_too_long"


class ChatBroadcastRefusedError(Exception):
    """A refusal with a stable code; the subclass picks the HTTP status."""

    def __init__(self, code: ChatBroadcastRefusalCode) -> None:
        self.code = code
        super().__init__(code)


class ChatBroadcastConflictError(ChatBroadcastRefusedError):
    """409: the campaign's state does not allow this right now."""


class ChatBroadcastInvalidError(ChatBroadcastRefusedError):
    """400: the request itself cannot be accepted."""
