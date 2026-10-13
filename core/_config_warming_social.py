"""Warming tunables for the pool, discussions, groups and live DMs (Прогрев 2.0, 4.5-4.7).

A mixin of :class:`core._config_warming.WarmingSettings`, like ``_config_warming_human``:
the fields are inherited, so their ``WARMING__*`` env names read like any other.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

_Phase = Literal["intro", "settling", "warming", "active", "warmed"]


class WarmingSocialSettings(BaseModel):
    """Channel-pool search, comment threads, public groups and the live side of DMs."""

    # Pool fill (4.5): what a found channel must look like to be proposed, how many are
    # probed per search, and the words that mark scams, 18+, casinos and crypto signals.
    pool_fill_min_subscribers: int = Field(default=2_000, ge=0)
    pool_fill_max_subscribers: int = Field(default=500_000, ge=1)
    pool_fill_alive_days: int = Field(default=14, ge=1)
    pool_fill_max_probes: int = Field(default=12, ge=1, le=50)
    pool_fill_blocked_words: list[str] = Field(
        default_factory=lambda: [
            "casino",
            "казино",
            "apuesta",
            "betting",
            "ставки",
            "18+",
            "xxx",
            "onlyfans",
            "porn",
            "sexy",
            "signals",
            "сигналы",
            "pump",
            "crypto signal",
            "forex",
            "earn money",
            "заработок",
            "dinero fácil",
            "giveaway",
        ],
    )
    # Discussions (4.6): chance a read channel's comments are opened, by visit kind.
    discussion_probability: float = Field(default=0.15, ge=0.0, le=1.0)
    discussion_binge_probability: float = Field(default=0.5, ge=0.0, le=1.0)
    # Public groups (4.6): joined and read from this phase on, reacted to this much less
    # often than channels, and never written in.
    group_min_phase: _Phase = "warming"
    group_reaction_factor: float = Field(default=0.3, ge=0.0, le=1.0)
    # Live DMs (4.7): instead of a text reply, sometimes a reaction or a sticker; an
    # opener may forward a post just read. Media only once the pair has this many
    # messages in its conversation window.
    dm_reaction_probability: float = Field(default=0.15, ge=0.0, le=1.0)
    dm_sticker_probability: float = Field(default=0.1, ge=0.0, le=1.0)
    dm_forward_probability: float = Field(default=0.1, ge=0.0, le=1.0)
    dm_media_min_exchanges: int = Field(default=4, ge=0)
    dm_reaction_emoji: list[str] = Field(
        min_length=1, default_factory=lambda: ["👍", "❤", "😂", "🔥", "😁"]
    )
    dm_sticker_moods: list[str] = Field(
        min_length=1, default_factory=lambda: ["😂", "👍", "😅", "❤", "🔥", "😊"]
    )

    @model_validator(mode="after")
    def _check_pool_bounds(self) -> WarmingSocialSettings:
        if self.pool_fill_min_subscribers > self.pool_fill_max_subscribers:
            msg = "pool_fill_min_subscribers must not exceed pool_fill_max_subscribers"
            raise ValueError(msg)
        return self
