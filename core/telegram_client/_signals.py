"""What each Telegram gateway tells spam intel about an account (docs/spam-intel.md, 5.3 and 5.9).

One place for the mapping from a raw observation to an evidence kind, so the gateways stay one
line each. Every function only OBSERVES something a call already returned: none makes a request,
none awaits, none raises (``core.spam_signals.emit_signal`` schedules and swallows).

Flood-wait is not evidence and is not reported here. PeerFlood is: on an action that sends a
message it is ``peer_flood_send``, on any other action ``peer_flood_other`` (weaker). A freeze and
a platform restriction are STATES, reported when seen and again, as their closer, when seen gone;
whether a closer matters is the receiver's call (it closes nothing that is not open).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.spam_signals import emit_signal
from schemas.spam_evidence import DETAIL_MAX_CHARS

if TYPE_CHECKING:
    from collections.abc import Mapping

    from schemas.spam_evidence import EvidenceKind

# Actions that send a message to another peer: a PeerFlood on one is a verdict on sending.
_SEND_ACTIONS = frozenset(
    {
        "post_comment",
        "comment_on_post",
        "send_dm",
        "send_chat_message",
        "copy_message_media",
        "broadcast_send_message",
        "broadcast_forward_post",
        "warm_dm_sticker",
        "warm_dm_forward",
    }
)
# Successes that prove nothing yet (weight 0): recorded so Stage 2 can measure their agreement
# with @SpamBot. Only actions whose target is provably what the kind says: a chat send may be to
# a user, a direct message may be to a known peer, so neither is listed.
_SHADOW_SUCCESS: dict[str, EvidenceKind] = {
    "post_comment": "group_send_ok",
    "comment_on_post": "group_send_ok",
    "broadcast_send_message": "group_send_ok",
    "broadcast_forward_post": "group_send_ok",
    "join_channel": "join_ok",
    "join_discussion_group": "join_ok",
    "broadcast_join_chatlist": "join_ok",
    "read_channel": "read_ok",
}
_FALLBACK_CODE = "restricted"


def signal_peer_flood(account_id: str, action_type: str) -> None:
    """``execute`` met PeerFlood on ``action_type``."""
    kind: EvidenceKind = "peer_flood_send" if action_type in _SEND_ACTIONS else "peer_flood_other"
    emit_signal(account_id, kind, source="execute", operation=action_type, detail="PeerFlood")


def signal_read_peer_flood(account_id: str) -> None:
    """``execute_read`` met PeerFlood on a read: the weak kind, whatever was read."""
    emit_signal(account_id, "peer_flood_other", source="execute_read", detail="PeerFlood")


def signal_success(account_id: str, action_type: str) -> None:
    """``execute`` finished ``action_type`` ok: a shadow success where the target is known."""
    kind = _SHADOW_SUCCESS.get(action_type)
    if kind is not None:
        emit_signal(account_id, kind, source="execute", operation=action_type)


def signal_frozen(account_id: str, *, source: str, operation: str | None, error: str) -> None:
    """A ``FROZEN_*`` answer: the account is frozen. ``error`` is the exception class name."""
    emit_signal(account_id, "freeze_seen", source=source, operation=operation, detail=error)


def signal_freeze_config(account_id: str, fields: Mapping[str, object]) -> None:
    """The app config the session check already fetched: a freeze is on it, or it is gone."""
    if not fields.get("freeze_since_date"):
        emit_signal(account_id, "freeze_cleared", source="session_check")
        return
    until = fields.get("freeze_until_date")
    detail = f"until={int(until)}" if isinstance(until, int) else None
    emit_signal(account_id, "freeze_seen", source="session_check", detail=detail)


def _restriction_reason(me: object) -> str:
    """The platform and reason CODES Telegram attached, never its free text."""
    reasons = getattr(me, "restriction_reason", None)
    codes = [
        f"{platform}:{reason}"
        for item in (reasons if isinstance(reasons, list | tuple) else ())
        if isinstance(platform := getattr(item, "platform", None), str)
        and isinstance(reason := getattr(item, "reason", None), str)
    ]
    return ",".join(codes)[:DETAIL_MAX_CHARS] or _FALLBACK_CODE


def signal_me(account_id: str, me: object) -> None:
    """``get_me`` already ran: ``restricted`` is a platform restriction, seen or gone.

    A flag that is not a bool (unset, or a test double) says nothing either way.
    """
    restricted = getattr(me, "restricted", None)
    if restricted is True:
        emit_signal(
            account_id, "tos_restricted", source="session_check", detail=_restriction_reason(me)
        )
    elif restricted is False:
        emit_signal(account_id, "tos_cleared", source="session_check")
