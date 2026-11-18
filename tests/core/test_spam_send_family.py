"""The send family of the PeerFlood signal is the union of the actions that send (audit F-028).

A PeerFlood on an action that sends a message is ``peer_flood_send`` (weight 0.7); on any other
it is the weaker ``peer_flood_other`` (0.4). The set that decides lives in
``core.telegram_client._signals``; this test keeps it in step with ``TelegramAction``: every action
type must be either in that set or in the reviewed list below, so a NEW sending action cannot be
left out by accident (it fails here until someone classifies it).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, get_args

import pytest

from core.spam_signals import drain_signals, set_signal_sink
from core.telegram_client._signals import SEND_ACTIONS, signal_peer_flood
from schemas.telegram_actions import TelegramAction

if TYPE_CHECKING:
    from schemas.spam_evidence import EvidenceKind

# Every action type that does NOT send a message to another peer, reviewed one by one: profile and
# privacy edits, joins and leaves, reads, reactions, stories, the own-channel admin calls, the
# warming browse/draft/note steps (a self-note or a draft reaches nobody), typing, and a button
# click (a callback, not a message). Add a new action to ``SEND_ACTIONS`` instead if it sends.
REVIEWED_NON_SENDING = frozenset(
    {
        "add_profile_music",
        "broadcast_join_chatlist",
        "channel_create",
        "channel_delete",
        "channel_edit",
        "channel_post_delete",
        "channel_post_edit",
        "channel_set_photo",
        "click_button",
        "join_channel",
        "join_discussion_group",
        "leave_channel",
        "leave_discussion_group",
        "manage_twofa_email",
        "mark_dm_read",
        "masstag_toggle_ttl",
        "outreach_set_typing",
        "post_story",
        "react_to_message",
        "react_to_post",
        "read_channel",
        "remove_profile_music",
        "remove_profile_photo",
        "remove_story",
        "set_main_profile_photo",
        "set_online",
        "set_privacy_settings",
        "set_profile_photo",
        "set_twofa_password",
        "toggle_story_pinned",
        "update_profile",
        "verify_twofa_password",
        "warm_browse_stickers",
        "warm_check_settings",
        "warm_consume_media",
        "warm_dm_react",
        "warm_emoji_status",
        "warm_forward_to_saved",
        "warm_get_dialogs",
        "warm_inline_query",
        "warm_link_preview",
        "warm_mute_peer",
        "warm_read_contacts",
        "warm_read_discussion",
        "warm_read_notify_settings",
        "warm_save_draft",
        "warm_saved_gifs",
        "warm_search_messages",
        "warm_self_note",
        "warm_toggle_archive",
        "warm_view_profile",
        "warm_vote_in_poll",
        "watch_peer_stories",
    }
)


def _members(annotation: Any) -> list[type]:
    """The model classes of a (nested, annotated) union."""
    found: list[type] = []
    for arg in get_args(annotation):
        if hasattr(arg, "model_fields"):
            found.append(arg)
        elif get_args(arg):
            found.extend(_members(arg))
    return found


def _action_types() -> set[str]:
    members = _members(get_args(TelegramAction)[0])
    return {str(member.model_fields["action_type"].default) for member in members}  # ty: ignore[unresolved-attribute]


def test_every_action_type_is_classified_as_sending_or_not() -> None:
    every = _action_types()

    assert not SEND_ACTIONS & REVIEWED_NON_SENDING, "an action cannot be both"
    assert every - SEND_ACTIONS - REVIEWED_NON_SENDING == set(), (
        "a new action type is not classified: add it to SEND_ACTIONS if it sends a message "
        "to another peer, else to REVIEWED_NON_SENDING"
    )
    assert (SEND_ACTIONS | REVIEWED_NON_SENDING) - every == set(), "a classified action is gone"


@pytest.mark.asyncio
async def test_a_peer_flood_on_a_channel_post_is_the_send_kind() -> None:
    """``channel_post_publish`` sends text or a file: it weighs 0.7, not the 0.4 of a join."""
    seen: list[EvidenceKind] = []

    async def sink(_account_id: str, kind: EvidenceKind, **_kwargs: object) -> None:
        seen.append(kind)

    set_signal_sink(sink)  # ty: ignore[invalid-argument-type]
    signal_peer_flood("a1", "channel_post_publish")
    signal_peer_flood("a1", "join_channel")
    await drain_signals()

    assert sorted(seen) == ["peer_flood_other", "peer_flood_send"]
