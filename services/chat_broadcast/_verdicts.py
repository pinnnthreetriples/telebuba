"""What one gateway answer means for the account, the chat and the step.

Grouped the way ``services.neuroshilling._telegram`` groups them, because the grouping
IS the policy: an account-wide refusal takes the account out and hands its chats to
others; a chat-wide one skips the chat for everyone; an answer whose outcome nobody
learnt is never repeated.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Literal

from core.telegram_client import UNCONFIRMED_ERROR_TYPE

if TYPE_CHECKING:
    from schemas.telegram_actions import ActionResult

SendVerdict = Literal[
    "sent",
    "unconfirmed",
    # Spam block, flood, frozen, logged out: the account leaves the campaign.
    "account_halt",
    # Slow mode in this chat: come back to it later.
    "chat_wait",
    # The connection dropped before the request left: retry after a pause.
    "reconnect",
    "admin_only",
    "banned",
    "not_member",
    "failed",
]
JoinVerdict = Literal[
    "joined",
    "already",
    "requested",
    "account_halt",
    "retry",
    "invalid_link",
    "banned",
    "failed",
]

_HALT_STATUSES: Final = frozenset({"flood_wait", "peer_flood", "premium_wait"})
_ACCOUNT_DEAD_MESSAGES: Final = frozenset({"account_deactivated", "account_frozen", "session_dead"})
# A gateway "this chat does not exist for this account" — a username nobody has, an
# invite nobody can open, an id the session never cached.
_NOT_FOUND_MESSAGE: Final = "chat_not_found"

_SEND_ERRORS: Final[dict[str, SendVerdict]] = {
    UNCONFIRMED_ERROR_TYPE: "unconfirmed",
    # The one account restriction that still arrives under its own class name: the
    # spam block that forbids writing to every group.
    "UserBannedInChannelError": "account_halt",
    "ChatWriteForbiddenError": "admin_only",
    "ChatSendPlainForbiddenError": "admin_only",
    "ChatSendMediaForbiddenError": "admin_only",
    "ChatSendPhotosForbiddenError": "admin_only",
    "ChatRestrictedError": "admin_only",
    "ChatGuestSendForbiddenError": "admin_only",
    "ChatAdminRequiredError": "admin_only",
    # Kicked from the chat, or the chat went private: a re-join would loop on the ban.
    "ChannelPrivateError": "banned",
    "UserNotParticipantError": "not_member",
    # How an id this account's session never cached reaches us.
    "ValueError": "not_member",
}

_JOIN_ERRORS: Final[dict[str, JoinVerdict]] = {
    "InviteRequestSentError": "requested",
    # ~500 chats: nothing this target did, and no other target will go better.
    "ChannelsTooMuchError": "account_halt",
    "InviteHashExpiredError": "invalid_link",
    "InviteHashInvalidError": "invalid_link",
    "InviteHashEmptyError": "invalid_link",
    "UsernameNotOccupiedError": "invalid_link",
    "UsernameInvalidError": "invalid_link",
    "InviteSlugExpiredError": "invalid_link",
    "InviteSlugEmptyError": "invalid_link",
    "ChannelPrivateError": "banned",
    "UserBannedInChannelError": "banned",
    "ChannelInvalidError": "invalid_link",
}


def classify_send(result: ActionResult) -> SendVerdict:  # noqa: PLR0911 - one per family
    if result.status == "ok":
        return "sent"
    if result.status in _HALT_STATUSES or result.error_message in _ACCOUNT_DEAD_MESSAGES:
        return "account_halt"
    if result.status == "slow_mode_wait":
        return "chat_wait"
    if result.error_type == UNCONFIRMED_ERROR_TYPE:
        return "unconfirmed"
    if result.status == "unavailable":
        return "reconnect"
    if result.error_message == _NOT_FOUND_MESSAGE:
        return "not_member"
    return _SEND_ERRORS.get(result.error_type or "", "failed")


def classify_join(result: ActionResult) -> JoinVerdict:
    if result.status == "ok":
        return "joined"
    if result.status == "already_participant":
        return "already"
    if result.status in _HALT_STATUSES or result.error_message in _ACCOUNT_DEAD_MESSAGES:
        return "account_halt"
    if result.status in {"slow_mode_wait", "unavailable"}:
        return "retry"
    if result.error_message == _NOT_FOUND_MESSAGE:
        return "invalid_link"
    return _JOIN_ERRORS.get(result.error_type or "", "failed")


def halt_reason(result: ActionResult) -> str:
    """A stable code for why the account left: the limit, the dead session, or the class."""
    if result.status in _HALT_STATUSES:
        return result.status
    if result.error_message in _ACCOUNT_DEAD_MESSAGES:
        return result.error_message or result.status
    return result.error_type or result.status
