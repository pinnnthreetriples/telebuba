"""Profile-media & story Telegram actions.

The cohesive cluster of ``TelegramAction`` / ``TelegramReadAction`` members that
drive profile photos, profile music, and stories. Split out of
``telegram_actions.py`` to keep that module under the file-size cap; the
discriminated unions there import these names back, so external callers keep
importing every action from ``schemas.telegram_actions`` unchanged.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class SetProfilePhoto(BaseModel):
    action_type: Literal["set_profile_photo"] = "set_profile_photo"
    filename: str = Field(min_length=1)
    content: bytes = Field(min_length=1)


class StoryMention(BaseModel):
    """A person tagged in the caption. ``user_id`` was resolved by the POSTING account."""

    user_id: int
    # Written into the caption as ``@username``; without one the first name is used.
    username: str | None = Field(default=None, max_length=64)
    first_name: str = Field(default="", max_length=64)


class StoryLink(BaseModel):
    """A clickable link area on the story (Premium only); ``text`` is drawn on photos."""

    url: str = Field(min_length=1, max_length=512)
    text: str = Field(default="", max_length=40)


class PostStory(BaseModel):
    action_type: Literal["post_story"] = "post_story"
    filename: str = Field(min_length=1)
    content: bytes = Field(min_length=1)
    media_kind: Literal["image", "video"]
    # 2048 is the Premium ceiling; a free account's 200 is the caller's to keep.
    caption: str | None = Field(default=None, max_length=2048)
    privacy_preset: Literal["contacts", "close_friends", "public"] = "contacts"
    # Up to 48 h, which only a Premium account may ask for.
    period_seconds: int = Field(default=86_400, ge=21_600, le=172_800)
    protect_content: bool = False
    # Collage images 2..N (``content`` is image #1). Non-empty turns the post
    # into a client-side collage: the gateway stitches them into ONE composite
    # photo (Telegram has no multi-photo story API). The business max count and
    # per-image validation live in config + the service, not here.
    extra_images: list[bytes] = Field(default_factory=list)
    collage_layout: str | None = None
    # Appended to the caption, each with a mention entity, so the person gets the push.
    mentions: list[StoryMention] = Field(default_factory=list, max_length=10)
    link: StoryLink | None = None

    @model_validator(mode="after")
    def _check_collage(self) -> PostStory:
        if self.extra_images and self.media_kind != "image":
            msg = "extra_images is only allowed for image stories"
            raise ValueError(msg)
        if not self.extra_images and self.collage_layout is not None:
            msg = "collage_layout requires extra_images"
            raise ValueError(msg)
        return self


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


class SetMainProfilePhoto(BaseModel):
    """Make an existing history photo the current avatar.

    True server semantics (live repro 2026-07-15, PR #255): the official
    promote (``photos.updateProfilePhoto``) mints a new id that INHERITS the
    original's date, so the promoted photo shows up mid-carousel instead of
    first and cached galleries display both ids of the same image. The gateway
    therefore RE-UPLOADS the photo's bytes as a brand-new photo — fresh date,
    first slide everywhere. The original stays in the history as a visible
    duplicate the operator can delete from the dashboard; this action itself
    deletes NOTHING (a post-promote "dedup" delete against a lagging read once
    destroyed an unrelated avatar — permanent data loss). The only avatar
    authority is ``UserFull.profile_photo.id``. Same ``InputPhoto`` triple as
    :class:`RemoveProfilePhoto`; all three id fields are required (the gateway
    re-resolves the target off a fresh ``GetUserPhotos`` read regardless, since
    the snapshot's ``file_reference`` can be stale).
    """

    action_type: Literal["set_main_profile_photo"] = "set_main_profile_photo"
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


class DownloadStoryMedia(BaseModel):
    """Read-only: download one own story's full media (photo or video).

    Backs the profile modal's story player — the snapshot only carries the
    poster thumbnail, so the playable file is fetched on demand.
    """

    action_type: Literal["download_story_media"] = "download_story_media"
    story_id: int = Field(gt=0)


class RemoveStory(BaseModel):
    """Delete one story from the account (active and/or pinned in one call).

    ``stories.deleteStories`` works for both states — there's no separate
    delete-pinned endpoint. Bad IDs are silently dropped server-side, so
    callers shouldn't try/except for ``STORY_ID_INVALID`` (the docs don't
    list it for this method).
    """

    action_type: Literal["remove_story"] = "remove_story"
    story_id: int = Field(gt=0)


class ToggleStoryPinned(BaseModel):
    """Pin a story to the profile (kept forever) or unpin it (expires in ≤24 h).

    ``stories.togglePinned`` flips ``StoryItem.pinned``: a pinned story stays in
    the profile's Stories grid past its active window, an unpinned one only
    survives as an active story until ``expire_date``. Idempotent — re-pinning
    an already-pinned story is a no-op server-side.
    """

    action_type: Literal["toggle_story_pinned"] = "toggle_story_pinned"
    story_id: int = Field(gt=0)
    pinned: bool


class WatchPeerStories(BaseModel):
    """View a subscribed peer's active stories and mark them seen.

    A low-risk, very human warming signal: ``stories.getPeerStories`` on a
    non-self peer, then ``stories.readStories`` up to the newest id. A no-op
    (still ``ok``) when the peer has no active stories.
    """

    action_type: Literal["watch_peer_stories"] = "watch_peer_stories"
    peer: str = Field(min_length=1)


class ListProfilePhotos(BaseModel):
    """Read-only: list the account's profile-photo history.

    Newest first. Each item carries the InputPhoto identifiers needed to
    delete it later via ``RemoveProfilePhoto``.
    """

    action_type: Literal["list_profile_photos"] = "list_profile_photos"
    limit: int = Field(default=24, ge=1, le=100)
