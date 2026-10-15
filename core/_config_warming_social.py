"""Warming tunables for the pool, discussions, groups and live DMs (Прогрев 2.0, 4.5-4.7).

A mixin of :class:`core._config_warming.WarmingSettings`, like ``_config_warming_human``:
the fields are inherited, so their ``WARMING__*`` env names read like any other.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

_Phase = Literal["intro", "settling", "warming", "active", "warmed"]

# Scam, 18+, casino and betting, crypto signals and easy money, in ru / es / en / pt.
_BLOCKED_WORDS = (
    # Casinos, slots and betting.
    "casino", "казино", "cassino", "slots", "слоты", "tragamonedas", "caça-níqueis",
    "jackpot", "джекпот", "poker", "покер", "póker", "pôquer", "betting", "беттинг",
    "bookmaker", "букмекер", "ставки на спорт", "ставка на спорт", "прогнозы на спорт",
    "каппер", "apuesta", "aposta", "pronósticos deportivos", "palpites", "1xbet", "1win",
    "mostbet", "melbet", "pin-up", "bet365", "betano", "aviator", "lucky jet",
    # Adult content.
    "18+", "xxx", "nsfw", "onlyfans", "porn", "порно", "эротик", "hentai", "sexy",
    "sexo", "секс", "nude", "desnud", "escort", "эскорт", "интим",
    "contenido adulto", "conteúdo adulto", "adult content",
    # Crypto signals, forex and pumps.
    "signals", "сигналы", "señales", "sinais", "crypto signal", "крипто сигнал",
    "pump signal", "памп", "forex", "форекс", "binary options", "бинарные опционы",
    "opciones binarias", "opções binárias", "airdrop", "аирдроп",
    # Easy money and giveaways.
    "earn money", "make money", "passive income", "заработок", "заработать", "заработай",
    "пассивный доход", "без вложений", "dinero fácil", "gana dinero", "ganar dinero",
    "ingresos pasivos", "dinheiro fácil", "ganhar dinheiro", "renda extra", "renda passiva",
    "giveaway", "розыгрыш", "sorteo", "sorteio",
)  # fmt: skip


class WarmingSocialSettings(BaseModel):
    """Channel-pool search, comment threads, public groups and the live side of DMs."""

    # Pool fill (4.5): what a found channel must look like to go into the pool, how many
    # are probed per search, and the words that mark scams, 18+, casinos, betting and
    # crypto signals. A word matches at the START of a word in the title or the about
    # text ("apuesta" catches "apuestas", "ставки на спорт" never "доставки"), so a stem
    # covers its endings and a short word does not fire inside an unrelated one.
    pool_fill_min_subscribers: int = Field(default=2_000, ge=0)
    pool_fill_max_subscribers: int = Field(default=500_000, ge=1)
    pool_fill_alive_days: int = Field(default=14, ge=1)
    pool_fill_max_probes: int = Field(default=12, ge=1, le=50)
    pool_fill_blocked_words: list[str] = Field(default_factory=lambda: list(_BLOCKED_WORDS))
    # A pool search (button or refill) gives its account back after this long at most.
    pool_fill_timeout_seconds: int = Field(default=900, ge=60)
    # The pause between two reads of a pool search: a person reading results.
    pool_fill_pause_seconds: tuple[float, float] = (6.0, 20.0)
    # Automatic refill: every ``pool_refill_check_minutes`` the most short language is
    # searched, but one language at most once per ``pool_refill_interval_hours``.
    pool_refill_enabled: bool = True
    pool_refill_check_minutes: int = Field(default=30, ge=5)
    pool_refill_interval_hours: int = Field(default=6, ge=1)
    pool_refill_startup_delay_seconds: int = Field(default=600, ge=0)
    # A warming account searching channels for itself: from this phase on, at most
    # ``self_search_daily_max`` times in 24 hours, on a normal or binge visit with this
    # chance, while it has fewer than ``self_search_target_found`` channels of its own
    # or its language is short. ``self_search_reserve_actions`` of the visit's budget
    # must stay left for the channels after the search.
    self_search_enabled: bool = True
    self_search_min_phase: _Phase = "warming"
    self_search_daily_max: int = Field(default=1, ge=0, le=3)
    self_search_probability: float = Field(default=0.3, ge=0.0, le=1.0)
    self_search_queries: int = Field(default=1, ge=1, le=3)
    self_search_max_probes: int = Field(default=3, ge=1, le=6)
    self_search_target_found: int = Field(default=2, ge=0)
    self_search_reserve_actions: int = Field(default=3, ge=0)
    # Three fruitless own searches in a week and the account stops looking until one ages out.
    self_search_fruitless_max: int = Field(default=3, ge=1)
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
