"""Warming *extras* toggles — one operator switch per side action of the cycle.

Data contract only (no behaviour). A ``TypedDict`` rather than ``dict[Literal, bool]``
because pydantic renders the former as a named object with one optional boolean per
key, so the generated frontend types carry the key names; the latter degrades to
``additionalProperties`` and the UI would lose the typed key set. Keys are the
snake_case of the catalog row keys in ``ActionTuningCard.tsx`` so no mapping table
exists anywhere.
"""

from __future__ import annotations

from typing import TypedDict

from pydantic import BaseModel, ConfigDict, with_config


class JoinedChannel(BaseModel):
    """One ``warming_joined_channels`` row; ``left_at`` set = left, in the re-join cooldown."""

    channel: str
    created_at: str
    left_at: str | None = None


# ``extra="forbid"``: an unknown key is a 422 at the API, never a silently stored typo.
@with_config(ConfigDict(extra="forbid"))
class ExtraToggles(TypedDict, total=False):
    """Which extras the operator allows. A missing key means "keep the stored value"."""

    # reading
    dialogs: bool
    search_messages: bool
    # activity
    polls: bool
    video: bool
    voice: bool
    # fun — traffic-heavy
    gif: bool
    stickers: bool
    inline_bots: bool
    link_preview: bool
    # social — every write here targets Saved Messages only
    forward: bool
    saved: bool
    contacts: bool
    scheduled: bool
    # chats
    leave: bool
    archive: bool
    mute: bool
    notifications: bool
    # profile
    view_profiles: bool
    check_settings: bool
    emoji_status: bool
    drafts: bool


# Light reads default ON: they are what every real client does on open and cost
# nothing. Traffic-heavy reads and every write default OFF so a deploy changes
# nothing for a live fleet until the operator opts in. Every key is present; a
# contract test pins this dict to the TypedDict's annotations.
EXTRA_TOGGLE_DEFAULTS: ExtraToggles = {
    "dialogs": True,
    "search_messages": True,
    "link_preview": True,
    "contacts": True,
    "notifications": True,
    "view_profiles": True,
    "check_settings": True,
    "gif": False,
    "stickers": False,
    "inline_bots": False,
    "polls": False,
    "video": False,
    "voice": False,
    "forward": False,
    "saved": False,
    "scheduled": False,
    "leave": False,
    "archive": False,
    "mute": False,
    "emoji_status": False,
    "drafts": False,
}
