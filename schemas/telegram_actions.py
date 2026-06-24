"""Typed Telegram actions.

Pydantic-described "do X on this account". Services and features never call
``client.send_message(...)`` directly — they build one of these classes and
hand it to ``core.telegram_client.execute(account_id, action)``.

Discriminator: ``action_type`` literal. First-cut set covers the actions
warming will need.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

# Runtime import (not TYPE_CHECKING): pydantic resolves the BotChallengeWaitResult
# field annotation at class-build time, so the type must exist at runtime.
from schemas.challenge import BotChallengeMessage  # noqa: TC001


class JoinChannel(BaseModel):
    action_type: Literal["join_channel"] = "join_channel"
    channel: str = Field(min_length=1)


class JoinDiscussionGroup(BaseModel):
    """Join the discussion group linked to ``channel`` (for commenting).

    The linked group usually has no username, so it can't be joined by handle.
    The gateway resolves it from the parent channel (``GetFullChannelRequest``)
    and joins the resolved ``Channel`` entity — entity juggling stays in core/.
    """

    action_type: Literal["join_discussion_group"] = "join_discussion_group"
    channel: str = Field(min_length=1)


class LeaveChannel(BaseModel):
    action_type: Literal["leave_channel"] = "leave_channel"
    channel: str = Field(min_length=1)


class PostComment(BaseModel):
    action_type: Literal["post_comment"] = "post_comment"
    chat_id: int
    text: str = Field(min_length=1)


class CommentOnPost(BaseModel):
    """Post a comment under a channel post via the linked discussion group.

    Telethon's ``send_message(channel, text, comment_to=post_id)`` routes the
    message into the channel's linked group; the account must already be a
    member of that group (onboarding handles the join).
    """

    action_type: Literal["comment_on_post"] = "comment_on_post"
    channel: str = Field(min_length=1)
    post_id: int
    text: str = Field(min_length=1)


class ClickButton(BaseModel):
    """Click an inline keyboard button on a message — e.g. a captcha prompt.

    Selector is index-first: ``button_index`` if given, else ``button_text``;
    with neither set the gateway clicks the first button.
    """

    action_type: Literal["click_button"] = "click_button"
    chat_id: int
    message_id: int
    button_index: int | None = None
    button_text: str | None = None


class UpdateProfile(BaseModel):
    action_type: Literal["update_profile"] = "update_profile"
    first_name: str = Field(min_length=1)
    last_name: str | None = None
    username: str | None = None
    bio: str | None = None


class SetOnline(BaseModel):
    """Flip the account's presence — warming uses it to look "active"."""

    action_type: Literal["set_online"] = "set_online"
    online: bool = True


class ReadChannel(BaseModel):
    """Fetch recent posts and mark them read — emulates a human reading a feed."""

    action_type: Literal["read_channel"] = "read_channel"
    channel: str = Field(min_length=1)
    message_limit: int = Field(default=15, ge=1, le=100)


class ReactToPost(BaseModel):
    """React to a random recent post with one of the candidate emojis."""

    action_type: Literal["react_to_post"] = "react_to_post"
    channel: str = Field(min_length=1)
    reactions: list[str] = Field(min_length=1)
    message_limit: int = Field(default=20, ge=1, le=100)


class SendDirectMessage(BaseModel):
    """Send a private message to another account — drives inter-account chat."""

    action_type: Literal["send_dm"] = "send_dm"
    user_id: int
    text: str = Field(min_length=1)


class SetProfilePhoto(BaseModel):
    action_type: Literal["set_profile_photo"] = "set_profile_photo"
    filename: str = Field(min_length=1)
    content: bytes = Field(min_length=1)


class PostStory(BaseModel):
    action_type: Literal["post_story"] = "post_story"
    filename: str = Field(min_length=1)
    content: bytes = Field(min_length=1)
    media_kind: Literal["image", "video"]
    caption: str | None = Field(default=None, max_length=1024)
    privacy_preset: Literal["contacts", "close_friends", "public"] = "contacts"
    period_seconds: int = Field(default=86_400, ge=21_600, le=86_400)
    protect_content: bool = False


class AddProfileMusic(BaseModel):
    action_type: Literal["add_profile_music"] = "add_profile_music"
    filename: str = Field(min_length=1)
    content: bytes = Field(min_length=1)
    title: str | None = Field(default=None, min_length=1)
    performer: str | None = Field(default=None, min_length=1)


class RemoveProfilePhoto(BaseModel):
    """Drops one photo from the account's profile-photo history.

    ``InputPhoto`` requires all three id fields. Removing the current avatar
    automatically promotes the previous photo to current (Telegram behavior).
    """

    action_type: Literal["remove_profile_photo"] = "remove_profile_photo"
    photo_id: int = Field(gt=0)
    access_hash: int
    file_reference: bytes = Field(min_length=1)


class RemoveProfileMusic(BaseModel):
    """Unpins one track from the account's saved profile music.

    All three identifier fields are required — Telethon's ``InputDocument``
    refuses partial refs. ``file_id`` alone is not enough; the read-side
    ``TelegramMusicItem`` carries ``access_hash`` and ``file_reference`` for
    exactly this reason.
    """

    action_type: Literal["remove_profile_music"] = "remove_profile_music"
    file_id: int = Field(gt=0)
    access_hash: int
    file_reference: bytes = Field(min_length=1)


class GetLinkedDiscussionGroup(BaseModel):
    """Read-only: resolve a channel's linked discussion group (for comments)."""

    action_type: Literal["get_linked_discussion_group"] = "get_linked_discussion_group"
    channel: str = Field(min_length=1)


class CheckMessagesAlive(BaseModel):
    """Read-only: re-read ``message_ids`` in ``channel``'s linked discussion group.

    The neurocomment deletion sweep posts comments via ``comment_to``, so they
    live in the channel's linked discussion group, not the broadcast channel.
    The gateway resolves that group and batch-reads the ids; a ``get_messages``
    ``None`` means the message was deleted/inaccessible → its id is returned in
    ``missing_ids``.
    """

    action_type: Literal["check_messages_alive"] = "check_messages_alive"
    channel: str = Field(min_length=1)
    message_ids: list[int]


class GetUserProfile(BaseModel):
    """Read-only: pull the signed-in user's own current profile state."""

    action_type: Literal["get_user_profile"] = "get_user_profile"


class ListPinnedStories(BaseModel):
    """Read-only: list the account's pinned-on-profile stories."""

    action_type: Literal["list_pinned_stories"] = "list_pinned_stories"
    limit: int = Field(default=20, ge=1, le=100)


class ListActiveStories(BaseModel):
    """Read-only: list the account's currently-active (≤24 h) stories.

    Mirrors what other users would see in the story ring on the profile —
    distinct from ``ListPinnedStories`` (permanent-on-profile). A story can
    appear in both lists; the service layer dedupes by ``story_id``.
    """

    action_type: Literal["list_active_stories"] = "list_active_stories"


class RemoveStory(BaseModel):
    """Delete one story from the account (active and/or pinned in one call).

    ``stories.deleteStories`` works for both states — there's no separate
    delete-pinned endpoint. Bad IDs are silently dropped server-side, so
    callers shouldn't try/except for ``STORY_ID_INVALID`` (the docs don't
    list it for this method).
    """

    action_type: Literal["remove_story"] = "remove_story"
    story_id: int = Field(gt=0)


class ListProfileMusic(BaseModel):
    """Read-only: list the music shown on the account's profile.

    Gracefully degrades when the installed Telethon version lacks the music TL
    methods — the gateway returns an empty list with ``supported=False``.
    """

    action_type: Literal["list_profile_music"] = "list_profile_music"


class ListProfilePhotos(BaseModel):
    """Read-only: list the account's profile-photo history.

    Newest first. Each item carries the InputPhoto identifiers needed to
    delete it later via ``RemoveProfilePhoto``.
    """

    action_type: Literal["list_profile_photos"] = "list_profile_photos"
    limit: int = Field(default=24, ge=1, le=100)


class WaitForBotChallenge(BaseModel):
    """Read-only: wait up to ``timeout_seconds`` for a guardian-bot challenge.

    Opens a short-lived ``NewMessage`` subscription on the just-joined discussion
    group ``chat_id`` and returns the first message that is a bot's inline-button
    challenge addressed to our account, or nothing on timeout (Ф2 #120).
    """

    action_type: Literal["wait_for_bot_challenge"] = "wait_for_bot_challenge"
    chat_id: int
    timeout_seconds: float = Field(gt=0)


TelegramAction = Annotated[
    JoinChannel
    | JoinDiscussionGroup
    | LeaveChannel
    | PostComment
    | CommentOnPost
    | ClickButton
    | UpdateProfile
    | SetOnline
    | ReadChannel
    | ReactToPost
    | SendDirectMessage
    | SetProfilePhoto
    | PostStory
    | AddProfileMusic
    | RemoveProfileMusic
    | RemoveProfilePhoto
    | RemoveStory,
    Field(discriminator="action_type"),
]

TelegramReadAction = Annotated[
    GetLinkedDiscussionGroup
    | CheckMessagesAlive
    | GetUserProfile
    | ListPinnedStories
    | ListActiveStories
    | ListProfileMusic
    | ListProfilePhotos
    | WaitForBotChallenge,
    Field(discriminator="action_type"),
]


class LinkedDiscussionGroupResult(BaseModel):
    """Gateway output for ``GetLinkedDiscussionGroup``.

    ``linked_chat_id`` is the discussion group's chat id, or ``None`` when the
    channel has comments disabled / no linked group.
    """

    linked_chat_id: int | None = None
    comments_enabled: bool


class CheckMessagesAliveResult(BaseModel):
    """Gateway output for ``CheckMessagesAlive`` — the ids that no longer exist."""

    missing_ids: list[int]


class BotChallengeWaitResult(BaseModel):
    """Gateway output for ``WaitForBotChallenge`` — the matched challenge or ``None``.

    A wrapper (not a bare ``BotChallengeMessage | None``) so ``execute_read`` keeps
    returning a ``BaseModel`` like every other read action.
    """

    message: BotChallengeMessage | None = None


ActionStatus = Literal[
    "ok",
    "flood_wait",
    "slow_mode_wait",
    "premium_wait",
    "peer_flood",
    "failed",
]


class ActionResult(BaseModel):
    """Outcome of one ``execute`` call."""

    status: ActionStatus
    action_type: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    message_id: int | None = None
    flood_wait_seconds: int | None = None
    error_type: str | None = None
    error_message: str | None = None


class NewPostEvent(BaseModel):
    """A fresh channel broadcast post surfaced by the push listener.

    Gateway output contract for ``subscribe_posts``: ``channel`` is the
    ORIGINAL subscription string the caller passed (not the resolved peer id)
    so the engine can map the post back to its campaign binding.
    """

    channel: str = Field(min_length=1)
    post_id: int
    text: str = ""
    has_media: bool = False
    is_forward: bool = False
