"""Warming *extras* actions — the side actions a real client performs between reads.

Sibling of ``schemas.telegram_actions`` (file-size cap); every class here is
re-exported from it and joins ``TelegramAction``. All ``action_type`` values carry
the ``warm_`` prefix: the write dispatcher routes the whole family through one
prefix-matched arm, and the gateway log rows come out as
``warming_telegram_warm_<...>``.

Reads and writes both ride the write executor deliberately: it is the only path
with the flood ladder, the ``ActionResult`` contract and free per-action log rows,
and warming never consumes the returned data. Whether a cycle books daily budget
for an action is the service's decision, not encoded here.

Contract rule: no model carries a destination peer for a self-scoped write —
``InputPeerSelf`` is hardcoded in ``core`` so a caller cannot aim a note, forward,
draft or reminder at another account.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class WarmGetDialogs(BaseModel):
    """Read: open the chat list, like launching the app (``messages.getDialogs``)."""

    action_type: Literal["warm_get_dialogs"] = "warm_get_dialogs"
    limit: int = Field(default=30, ge=1, le=100)


class WarmReadContacts(BaseModel):
    """Read-only contact sync (``contacts.getContacts`` + ``getStatuses``); never imports."""

    action_type: Literal["warm_read_contacts"] = "warm_read_contacts"


class WarmReadNotifySettings(BaseModel):
    """Read the three global notification scopes (broadcasts / users / chats)."""

    action_type: Literal["warm_read_notify_settings"] = "warm_read_notify_settings"


class WarmCheckSettings(BaseModel):
    """Read a random bundle of ``calls`` settings screens (privacy, sessions, app config...)."""

    action_type: Literal["warm_check_settings"] = "warm_check_settings"
    calls: int = Field(default=3, ge=1, le=7)
    # Where in the 11-entry read table the bundle starts. Randomness lives in the
    # service; core stays deterministic and only rotates from here.
    offset: int = Field(default=0, ge=0, le=10)


class WarmViewProfile(BaseModel):
    """Read one profile: our own (``kind="self"``) or a channel we read (``kind="channel"``).

    DM peers and comment authors are deliberately not addressable — viewing them
    would tie pool accounts together.
    """

    action_type: Literal["warm_view_profile"] = "warm_view_profile"
    kind: Literal["self", "channel"] = "self"
    channel: str | None = None


WarmingAction = Annotated[
    WarmGetDialogs
    | WarmReadContacts
    | WarmReadNotifySettings
    | WarmCheckSettings
    | WarmViewProfile,
    Field(discriminator="action_type"),
]
