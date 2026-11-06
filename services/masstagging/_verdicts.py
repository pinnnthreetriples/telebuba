"""What one gateway answer means for the account and the person.

Grouped the way ``services.chat_broadcast._verdicts`` groups them, because the grouping
IS the policy: a flood pauses the account and the people wait; a peer flood is a spam
block and takes the account out; a person who closed their DMs fails alone; an answer
nobody received is never repeated.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Literal

from core.telegram_client import UNCONFIRMED_ERROR_TYPE

if TYPE_CHECKING:
    from schemas.telegram_actions import ActionResult

Verdict = Literal[
    "ok",
    # Telegram asked to wait: the account pauses, the people wait for an account.
    "flood",
    # A peer flood is the spam block: the account leaves the run.
    "spamblock",
    # A dead, frozen or logged-out session: the account leaves the run.
    "dead",
    # The connection dropped before the request left: try again later.
    "reconnect",
    # The person does not let this account write or tag them.
    "privacy",
    # The person's account is gone.
    "gone",
    # The link or the 48 h lifetime needs Premium: post again without them.
    "premium",
    # The account's stories for today are spent.
    "too_much",
    # Sent, but the answer was lost.
    "unconfirmed",
    "error",
]

_WAIT_STATUSES: Final = frozenset({"flood_wait", "premium_wait", "slow_mode_wait"})
_DEAD_MESSAGES: Final = frozenset({"account_deactivated", "account_frozen", "session_dead"})
# The gateway turns a refused story into ``ProfileGatewayError`` carrying a lowercase stable
# code (``core.telegram_client._media``): Premium-only, and the day's story quota.
_GATEWAY_ERROR: Final = "ProfileGatewayError"
_GATEWAY_CODES: Final[dict[str, Verdict]] = {
    "premium_required": "premium",
    "stories_too_much": "too_much",
}
_ERRORS: Final[dict[str, Verdict]] = {
    "UserPrivacyRestrictedError": "privacy",
    "UserNotMutualContactError": "privacy",
    "UserIsBlockedError": "privacy",
    "YouBlockedUserError": "privacy",
    "UserBlockedError": "privacy",
    "ChatWriteForbiddenError": "privacy",
    "InputUserDeactivatedError": "gone",
    "UserIdInvalidError": "gone",
    "PremiumAccountRequiredError": "premium",
}


def classify(result: ActionResult) -> Verdict:  # noqa: PLR0911 - one return per family
    if result.status == "ok":
        return "ok"
    if result.status in _WAIT_STATUSES:
        return "flood"
    if result.status == "peer_flood":
        return "spamblock"
    if result.error_message in _DEAD_MESSAGES:
        return "dead"
    if result.error_type == UNCONFIRMED_ERROR_TYPE:
        return "unconfirmed"
    if result.status == "unavailable":
        return "reconnect"
    if result.error_type == _GATEWAY_ERROR and result.error_message in _GATEWAY_CODES:
        return _GATEWAY_CODES[result.error_message]
    return _ERRORS.get(result.error_type or "", "error")
