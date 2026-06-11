"""Pydantic schemas for the account-warming domain.

These models flow between ``features/warming.py`` (UI), ``services/warming.py``
(business logic), and ``core/db.py`` (persistence). No behaviour, no I/O — just
the data contract per non-negotiable #2.

Warming lifecycle (``WarmingState``):

- ``idle``      — account sits in the left kanban column, no loop running.
- ``active``    — currently performing a warming cycle (joining / reading / reacting).
- ``sleeping``  — cycle finished, waiting 12-30h before the next one.
- ``flood_wait``— Telegram rate-limited the account; cooling down.
- ``error``     — last cycle failed; needs attention.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

WarmingState = Literal["idle", "active", "sleeping", "flood_wait", "error"]
WarmingHealth = Literal["idle", "ok", "warn", "fail"]

_ACTIVE_STATES: frozenset[WarmingState] = frozenset({"active", "sleeping", "flood_wait", "error"})


def warming_health(state: WarmingState) -> WarmingHealth:
    """Map a warming state to a traffic-light colour for the UI.

    - ``ok`` (green)  — actively warming.
    - ``warn`` (amber) — sleeping between cycles or flood-waited.
    - ``fail`` (red)  — last cycle errored.
    - ``idle`` (grey) — not being warmed.
    """
    if state == "active":
        return "ok"
    if state in {"sleeping", "flood_wait"}:
        return "warn"
    if state == "error":
        return "fail"
    return "idle"


def is_warming(state: WarmingState) -> bool:
    """True when the account belongs in the right-hand (warming) kanban column."""
    return state in _ACTIVE_STATES


class WarmingChannel(BaseModel):
    """One channel the warming engine can join / read / react in."""

    channel: str = Field(min_length=1)
    label: str | None = None
    created_at: str = Field(min_length=1)


class WarmingChannelList(BaseModel):
    channels: list[WarmingChannel] = Field(default_factory=list)


class AddChannelsRequest(BaseModel):
    """Raw user input — one or many links/usernames, newline- or comma-separated."""

    model_config = ConfigDict(extra="forbid")

    raw: str = Field(min_length=1)


class RemoveChannelRequest(BaseModel):
    channel: str = Field(min_length=1)


class WarmingSettings(BaseModel):
    """Masked, UI-facing warming settings — never carries the raw Gemini key."""

    inter_account_chat: bool = False
    reactions_enabled: bool = True
    has_gemini_key: bool = False
    gemini_model: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)


class WarmingSettingsSecret(BaseModel):
    """Internal read model — carries the raw Gemini key for ``core`` gateways only."""

    inter_account_chat: bool
    reactions_enabled: bool
    gemini_api_key: str
    gemini_model: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)


class WarmingSettingsUpdate(BaseModel):
    """Caller-supplied settings change from the UI.

    ``gemini_api_key`` semantics: ``None`` leaves the stored key untouched, an
    empty string clears it, any other value replaces it.
    """

    model_config = ConfigDict(extra="forbid")

    inter_account_chat: bool = False
    reactions_enabled: bool = True
    gemini_api_key: str | None = None


class WarmingStateRecord(BaseModel):
    """One row of the ``warming_account_state`` table (no account metadata)."""

    account_id: str = Field(min_length=1)
    state: WarmingState
    cycles_completed: int = Field(default=0, ge=0)
    last_event: str | None = None
    last_cycle_at: str | None = None
    next_run_at: str | None = None
    updated_at: str = Field(min_length=1)


class WarmingStateWrite(BaseModel):
    """Caller-supplied warming-state change; ``core.db`` stamps ``updated_at``."""

    account_id: str = Field(min_length=1)
    state: WarmingState
    cycles_completed: int = Field(default=0, ge=0)
    last_event: str | None = None
    last_cycle_at: str | None = None
    next_run_at: str | None = None


class WarmingAccountState(BaseModel):
    """One account's warming status, rendered as a kanban card."""

    account_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    state: WarmingState
    health: WarmingHealth
    cycles_completed: int = Field(default=0, ge=0)
    last_event: str | None = None
    last_cycle_at: str | None = None
    next_run_at: str | None = None
    updated_at: str | None = None


class WarmingBoardState(BaseModel):
    """Everything the warming page renders in one poll tick."""

    idle: list[WarmingAccountState] = Field(default_factory=list)
    warming: list[WarmingAccountState] = Field(default_factory=list)
    channels: WarmingChannelList
    settings: WarmingSettings
    channel_count: int = Field(ge=0)
    active_count: int = Field(ge=0)


class StartWarmingRequest(BaseModel):
    account_id: str = Field(min_length=1)


class StopWarmingRequest(BaseModel):
    account_id: str = Field(min_length=1)


class WarmingCycleRequest(BaseModel):
    account_id: str = Field(min_length=1)


CycleStatus = Literal["ok", "skipped", "flood_wait", "error"]


class WarmingCycleResult(BaseModel):
    """Outcome of one warming cycle — drives counters and the activity log."""

    account_id: str = Field(min_length=1)
    status: CycleStatus
    channels_joined: int = Field(default=0, ge=0)
    channels_read: int = Field(default=0, ge=0)
    reactions_sent: int = Field(default=0, ge=0)
    messages_sent: int = Field(default=0, ge=0)
    detail: str | None = None
