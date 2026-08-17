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

from schemas import telegram_action_results as _telegram_results
from schemas.accounts import (
    PROFILE_BIO_MAX_LENGTH,
    PROFILE_NAME_MAX_LENGTH,
    PROFILE_USERNAME_PATTERN,
)

# Privacy, write-rights and channel-activity reads likewise live in sibling modules; the
# unions below reference them, so importing here keeps the original import paths working.
from schemas.telegram_actions_activity import GetLastPostAt

# The channel-management action cluster lives in a sibling module (file-size
# cap); importing the names here keeps
# ``from schemas.telegram_actions import CreateChannel`` working unchanged.
from schemas.telegram_actions_channels import (
    CheckChannelUsername,
    CreateChannel,
    DeleteChannel,
    DeleteChannelPost,
    EditChannel,
    EditChannelPost,
    GetOwnChannel,
    ListChannelPosts,
    ListOwnChannels,
    PublishChannelPost,
    SetChannelPhoto,
)

# The chat-scoped cluster (resolve / react-in-place / copy media / read back) is a
# sibling module too; the unions below reference every name.
from schemas.telegram_actions_chat import (
    CopyMessageMedia,
    ReactToMessage,
    ReadChatMessages,
    ResolveChat,
)

# The comment cluster (the write action and the thread read) is a sibling module too;
# ``PostMediaKind`` went with it, being a classification of what a comment could use.
from schemas.telegram_actions_comments import CommentOnPost, ReadPostComments

# The channel-discovery read cluster likewise lives in a sibling module; the read
# union below references every name.
from schemas.telegram_actions_discovery import (
    GetSimilarChannels,
    SearchChannels,
    SearchGlobalPosts,
)

# The profile-media / story action cluster lives in a sibling module (file-size
# cap); the discriminated unions below reference every name, so importing them
# here keeps ``from schemas.telegram_actions import PostStory`` working unchanged.
from schemas.telegram_actions_media import (
    AddProfileMusic,
    ListActiveStories,
    ListPinnedStories,
    ListProfilePhotos,
    PostStory,
    RemoveProfileMusic,
    RemoveProfilePhoto,
    RemoveStory,
    SetMainProfilePhoto,
    SetProfilePhoto,
    ToggleStoryPinned,
    WatchPeerStories,
)
from schemas.telegram_actions_privacy import GetPrivacySettings, SetPrivacySettings
from schemas.telegram_actions_rights import CheckWriteRights

ActionResult = _telegram_results.ActionResult
ActionStatus = _telegram_results.ActionStatus
BanCheckResult = _telegram_results.BanCheckResult
BotChallengeWaitResult = _telegram_results.BotChallengeWaitResult
ChatMessagePreview = _telegram_results.ChatMessagePreview
CheckMessagesAliveResult = _telegram_results.CheckMessagesAliveResult
LinkedDiscussionGroupResult = _telegram_results.LinkedDiscussionGroupResult
NewPostEvent = _telegram_results.NewPostEvent
PostImageResult = _telegram_results.PostImageResult
PostMediaKind = _telegram_results.PostMediaKind
ReadChatMessagesResult = _telegram_results.ReadChatMessagesResult
ResolveChatResult = _telegram_results.ResolveChatResult


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


class LeaveDiscussionGroup(BaseModel):
    """Leave the discussion group linked to ``channel`` — mirror of ``JoinDiscussionGroup``.

    ``LeaveChannel`` cannot do this: it issues ``LeaveChannelRequest`` against the
    broadcast channel named by the handle, while the commenting membership lives in
    the linked group, which usually has no username of its own. The gateway resolves
    that group from the parent channel and leaves the resolved entity.
    """

    action_type: Literal["leave_discussion_group"] = "leave_discussion_group"
    channel: str = Field(min_length=1)


class PostComment(BaseModel):
    """Send a message into a chat this account is already inside.

    ``chat_id`` is the raw positive id — see ``schemas.telegram_actions_chat`` for
    the pinned convention and for why one account's id is useless to another.
    """

    action_type: Literal["post_comment"] = "post_comment"
    chat_id: int
    text: str = Field(min_length=1)
    # Aims the message at an existing one in the SAME chat, which is what turns a
    # list of sends into a staged conversation. ``CommentOnPost.reply_to`` is a
    # different action with a different peer (a channel's linked group); the two are
    # not interchangeable.
    reply_to: int | None = None


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
    """Update profile text. Field contract: ``""`` clears, ``None`` leaves unchanged."""

    action_type: Literal["update_profile"] = "update_profile"
    first_name: str = Field(min_length=1, max_length=PROFILE_NAME_MAX_LENGTH)
    last_name: str | None = Field(default=None, max_length=PROFILE_NAME_MAX_LENGTH)
    username: str | None = Field(default=None, pattern=PROFILE_USERNAME_PATTERN)
    bio: str | None = Field(default=None, max_length=PROFILE_BIO_MAX_LENGTH)


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
    # Candidate post ids from a preceding read; when set the reactor picks from
    # these instead of re-fetching the channel (``message_limit`` then unused).
    message_ids: list[int] | None = None


class SendDirectMessage(BaseModel):
    """Send a private message to another account — drives inter-account chat."""

    action_type: Literal["send_dm"] = "send_dm"
    user_id: int
    text: str = Field(min_length=1)
    # Per-account typing tempo (WPM) for the "typing…" simulation; ``None`` falls
    # back to the global ``typing_wpm``.
    typing_wpm: int | None = None
    # Recipient's phone, used to teach a fresh session the peer's access_hash
    # (a raw user_id it has never seen cannot be resolved). ``None`` = resolve
    # from the session cache only.
    peer_phone: str | None = None


class MarkDirectMessageRead(BaseModel):
    """Mark a private conversation read — emulates opening a DM before replying."""

    action_type: Literal["mark_dm_read"] = "mark_dm_read"
    user_id: int
    # Same role as on ``SendDirectMessage`` — peer resolution for a cold session.
    peer_phone: str | None = None


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


class CheckBannedInChannel(BaseModel):
    """Read-only: is this account banned / write-forbidden in ``channel``?

    Comments are posted into the channel's linked discussion group, so the ban
    lives there, not on the broadcast channel. The gateway resolves that group
    (like ``CheckMessagesAlive``) and probes the account's own participant state
    via ``GetParticipantRequest`` — a pure read, no message is sent.
    """

    action_type: Literal["check_banned_in_channel"] = "check_banned_in_channel"
    channel: str = Field(min_length=1)


class GetUserProfile(BaseModel):
    """Read-only: pull the signed-in user's own current profile state."""

    action_type: Literal["get_user_profile"] = "get_user_profile"


class ListProfileMusic(BaseModel):
    """Read-only: list the music shown on the account's profile.

    Gracefully degrades when the installed Telethon version lacks the music TL
    methods — the gateway returns an empty list with ``supported=False``.
    """

    action_type: Literal["list_profile_music"] = "list_profile_music"


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
    | LeaveDiscussionGroup
    | PostComment
    | CommentOnPost
    | ClickButton
    | UpdateProfile
    | SetPrivacySettings
    | SetOnline
    | ReadChannel
    | ReactToPost
    | ReactToMessage
    | CopyMessageMedia
    | SendDirectMessage
    | MarkDirectMessageRead
    | SetProfilePhoto
    | PostStory
    | AddProfileMusic
    | RemoveProfileMusic
    | RemoveProfilePhoto
    | SetMainProfilePhoto
    | RemoveStory
    | ToggleStoryPinned
    | WatchPeerStories
    | CreateChannel
    | EditChannel
    | SetChannelPhoto
    | DeleteChannel
    | PublishChannelPost
    | EditChannelPost
    | DeleteChannelPost,
    Field(discriminator="action_type"),
]

TelegramReadAction = Annotated[
    GetLinkedDiscussionGroup
    | CheckMessagesAlive
    | CheckBannedInChannel
    | CheckWriteRights
    | ReadPostComments
    | ResolveChat
    | ReadChatMessages
    | GetUserProfile
    | GetPrivacySettings
    | ListPinnedStories
    | ListActiveStories
    | ListProfileMusic
    | ListProfilePhotos
    | WaitForBotChallenge
    | ListOwnChannels
    | GetOwnChannel
    | ListChannelPosts
    | CheckChannelUsername
    | SearchChannels
    | GetSimilarChannels
    | SearchGlobalPosts
    | GetLastPostAt,
    Field(discriminator="action_type"),
]
