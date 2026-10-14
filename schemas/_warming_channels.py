"""Warming channel models — split from ``schemas.warming`` for the file-size budget.

Re-exported there, so ``from schemas.warming import WarmingChannel`` keeps working.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

WarmingChannelKind = Literal["channel", "group"]
# Where the row came from: typed by the operator, found by a pool search («Найти
# каналы» or the automatic refill), or found by a warming account searching for itself.
WarmingChannelSource = Literal["manual", "discovery", "self_search"]
# Everything a search finds that passes the filters is ``approved`` at once. A found
# channel the operator removes stays as ``removed`` so no later search brings it back;
# it is never listed or walked, and typing it in again restores it.
WarmingChannelStatus = Literal["approved", "removed"]

# ISO 639-1 language and ISO 3166 alpha-2 country, lower- and upper-case as stored.
# ``other`` is a script the detector cannot name (CJK, Arabic...): known foreign to
# every account, unlike ``None``, which stays everyone's fallback.
_LANGUAGE_PATTERN = r"^(?:[a-z]{2}|other)$"
_COUNTRY_PATTERN = r"^[A-Z]{2}$"


class WarmingChannel(BaseModel):
    """One channel the warming engine can join / read / react in.

    ``language`` is what an account matches its own phone language against; ``None``
    means nobody has told yet (no read has labelled it, the operator has not set it),
    and such a channel stays a fallback for any account.
    """

    channel: str = Field(min_length=1)
    label: str | None = None
    created_at: str = Field(min_length=1)
    language: str | None = Field(default=None, pattern=_LANGUAGE_PATTERN)
    country: str | None = Field(default=None, pattern=_COUNTRY_PATTERN)
    kind: WarmingChannelKind | None = None
    subscribers: int | None = Field(default=None, ge=0)
    last_post_at: str | None = None
    source: WarmingChannelSource = "manual"
    status: WarmingChannelStatus = "approved"
    # The warming account whose own search found it; that account prefers it.
    found_by: str | None = None


class WarmingChannelList(BaseModel):
    channels: list[WarmingChannel] = Field(default_factory=list)


class UpdateChannelRequest(BaseModel):
    """The operator's labels for one channel; ``None`` clears a label back to unknown."""

    model_config = ConfigDict(extra="forbid")

    channel: str = Field(min_length=1)
    language: str | None = Field(default=None, pattern=_LANGUAGE_PATTERN)
    country: str | None = Field(default=None, pattern=_COUNTRY_PATTERN)


class WarmingChannelObservation(BaseModel):
    """What one warming read learnt about a channel, written back onto its row."""

    language: str | None = Field(default=None, pattern=_LANGUAGE_PATTERN)
    kind: WarmingChannelKind | None = None
    last_post_at: str | None = None


class ChannelLanguageShortage(BaseModel):
    """A phone language whose accounts have fewer channels of their own than the floor."""

    language: str = Field(min_length=1)
    channels: int = Field(ge=0)
    accounts: int = Field(ge=1)
