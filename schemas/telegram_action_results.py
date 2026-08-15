"""Result and event models emitted by the Telegram gateway."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from schemas.challenge import BotChallengeMessage  # noqa: TC001
from schemas.telegram_actions_comments import PostMediaKind  # noqa: TC001


class LinkedDiscussionGroupResult(BaseModel):
    """Gateway output for ``GetLinkedDiscussionGroup``.

    ``linked_chat_id`` is the discussion group's chat id, or ``None`` when the
    channel has comments disabled / no linked group.

    Every optional field below rides the same ``channels.getFullChannel`` reply, so
    ``None`` always means "the reply did not answer this" (no linked group, an older
    TL layer, a field Telegram omitted) and never "no" — a caller that blocks a
    campaign must test for the positive verdict, not for falsiness.
    """

    linked_chat_id: int | None = None
    comments_enabled: bool
    # Free ride: ``channels.getFullChannel`` already returns the subscriber count,
    # so discovery backfills it here instead of spending a second RPC.
    participants_count: int | None = None
    # The next three come off the LINKED GROUP's ``Channel``: the signals that decide
    # whether a campaign can actually comment, learnt at discovery instead of when
    # the live campaign fails against the channel.
    #
    # Commenting requires joining the group first.
    join_to_send: bool | None = None
    # Joining needs an admin's approval — a dead end for an unattended campaign.
    join_request: bool | None = None
    # Positive sense on purpose: the wire carries ``default_banned_rights.send_messages``
    # ("writing is banned for everyone"), and a caller should not have to unpick a
    # double negative to answer "may we write here at all".
    can_send_messages: bool | None = None
    # Slow mode is ON in the discussion group; its interval is not in this reply, and
    # re-reading it would cost a second ``getFullChannel``.
    group_slowmode_enabled: bool | None = None
    # Telegram's marks on the BROADCAST channel itself, which is the entity the operator
    # adopts. Read off the group they described the wrong channel in both directions.
    scam: bool | None = None
    fake: bool | None = None
    restricted: bool | None = None


class CheckMessagesAliveResult(BaseModel):
    """Gateway output for ``CheckMessagesAlive`` — the ids that no longer exist."""

    missing_ids: list[int]


class BanCheckResult(BaseModel):
    """Gateway output for ``CheckBannedInChannel`` — the account's participant state.

    ``can_send`` = a member able to comment; ``restricted`` = banned from sending;
    ``not_member`` = kicked / no longer a participant; ``comments_disabled`` = the
    channel has no linked discussion group / comments off (can't be checked).
    """

    state: Literal["can_send", "restricted", "not_member", "comments_disabled"]


class BotChallengeWaitResult(BaseModel):
    """Gateway output for ``WaitForBotChallenge`` — the matched challenge or ``None``.

    A wrapper (not a bare ``BotChallengeMessage | None``) so ``execute_read`` keeps
    returning a ``BaseModel`` like every other read action.
    """

    message: BotChallengeMessage | None = None


ActionStatus = Literal[
    "ok",
    # A join RPC against a channel/group the account is already a member of: a
    # success everywhere (it IS joined), but a no-op the caller can tell apart
    # from a real join so it does not count against the rolling-24h join cap.
    "already_participant",
    "flood_wait",
    "slow_mode_wait",
    "premium_wait",
    "peer_flood",
    "failed",
    # Infrastructure failure (client pool / socket / timeout) — the account and
    # the request are fine; the API maps this to 503, never a 400 client fault.
    "unavailable",
]


class ActionResult(BaseModel):
    """Outcome of one ``execute`` call."""

    status: ActionStatus
    action_type: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    message_id: int | None = None
    # The new channel's id, set only by ``channel_create``. Telegram ids are
    # int64 (past JS's 2^53 safe-integer window), so it crosses the JSON
    # boundary as a decimal string — same rationale as profile_media._Int64Str.
    channel_id: str | None = None
    # Recent post ids a read fetched, so a following react reuses them instead of
    # re-fetching. int64 ids cross the JSON boundary as decimal strings, same
    # rationale as ``channel_id``.
    recent_message_ids: list[str] | None = None
    flood_wait_seconds: int | None = None
    # Privacy keys that DID apply before a ``set_privacy_settings`` was refused
    # (``account.setPrivacy`` is one call per key with no rollback). ``None`` for
    # every other action, and for a privacy write that failed on its first key.
    applied_privacy_keys: list[str] | None = None
    error_type: str | None = None
    error_message: str | None = None


class PostImageResult(BaseModel):
    """Gateway output for ``download_post_image`` — the photo, or why there isn't one.

    ``reason`` is set exactly when ``image_b64`` is ``None``: ``unavailable`` (the post is
    gone, carries no photo, the download yielded no bytes, the gateway faulted, or the
    fetch outstayed its deadline) or ``too_large`` (the photo offers no size at all under
    the caller's byte ceiling — an oversized ORIGINAL is not refused, it rides along as
    its biggest size that fits). The caller turns it into a post-skip reason, so the
    operator sees which of the two happened.
    """

    image_b64: str | None = None
    reason: Literal["unavailable", "too_large"] | None = None


class NewPostEvent(BaseModel):
    """A fresh channel broadcast post surfaced by the push listener.

    Gateway output contract for ``subscribe_posts``: ``channel`` is the
    ORIGINAL subscription string the caller passed (not the resolved peer id)
    so the engine can map the post back to its campaign binding.
    """

    channel: str = Field(min_length=1)
    post_id: int
    text: str = ""
    media_kind: PostMediaKind = "none"
    is_forward: bool = False
    # Telegram's source timestamp. Zero is accepted for older callers/tests, but the
    # live listener and bounded startup backfill always populate it. The durable inbox
    # uses it to refuse stale history rather than commenting on an old post after boot.
    date_unix: int = Field(default=0, ge=0)
