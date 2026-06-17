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

WarmingState = Literal["idle", "active", "sleeping", "flood_wait", "quarantine", "error"]
WarmingHealth = Literal["idle", "ok", "warn", "fail"]

_ACTIVE_STATES: frozenset[WarmingState] = frozenset(
    {"active", "sleeping", "flood_wait", "quarantine", "error"},
)


def warming_health(state: WarmingState) -> WarmingHealth:
    """Map a warming state to a traffic-light colour for the UI.

    - ``ok`` (green)  — actively warming.
    - ``warn`` (amber) — sleeping between cycles, flood-waited, or quarantined.
    - ``fail`` (red)  — last cycle errored.
    - ``idle`` (grey) — not being warmed.
    """
    if state == "active":
        return "ok"
    if state in {"sleeping", "flood_wait", "quarantine"}:
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
    join_enabled: bool = True
    enforce_readiness: bool = True
    quiet_hours_enabled: bool = False
    quiet_hours_start: int = Field(default=0, ge=0, le=23)
    quiet_hours_end: int = Field(default=0, ge=0, le=23)
    max_daily_actions: int = Field(default=0, ge=0)
    has_gemini_key: bool = False
    gemini_model: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)


class WarmingSettingsSecret(BaseModel):
    """Internal read model — carries the raw Gemini key for ``core`` gateways only."""

    inter_account_chat: bool
    reactions_enabled: bool
    join_enabled: bool = True
    enforce_readiness: bool = True
    quiet_hours_enabled: bool = False
    quiet_hours_start: int = Field(default=0, ge=0, le=23)
    quiet_hours_end: int = Field(default=0, ge=0, le=23)
    max_daily_actions: int = Field(default=0, ge=0)
    gemini_api_key: str
    gemini_model: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)


class WarmingSettingsUpdate(BaseModel):
    """Caller-supplied settings change from the UI.

    ``gemini_api_key`` semantics: ``None`` leaves the stored key untouched, an
    empty string clears it, any other value replaces it. Same applies to
    ``gemini_model`` — ``None`` keeps current value, non-empty overrides.
    An explicit ``clear_gemini_key`` flag is provided so the UI can clear the
    stored key without ambiguity.
    """

    model_config = ConfigDict(extra="forbid")

    inter_account_chat: bool = False
    reactions_enabled: bool = True
    join_enabled: bool = True
    enforce_readiness: bool = True
    quiet_hours_enabled: bool = False
    quiet_hours_start: int = Field(default=0, ge=0, le=23)
    quiet_hours_end: int = Field(default=0, ge=0, le=23)
    max_daily_actions: int = Field(default=0, ge=0)
    gemini_api_key: str | None = None
    gemini_model: str | None = None
    clear_gemini_key: bool = False


class WarmingIntensity(BaseModel):
    """Effective per-cycle intensity for an account, derived from its age.

    Produced by the age-based ramp: a fresh account warms quietly (few channels,
    low reaction rate, no DM) and grows to the configured full intensity.
    """

    channels_min: int = Field(ge=1)
    channels_max: int = Field(ge=1)
    reaction_probability: float = Field(ge=0.0, le=1.0)
    dm_allowed: bool


class WarmingReadiness(BaseModel):
    """Pre-start verdict for an account: can it safely begin warming?

    ``reasons`` is empty iff ``ready`` is True — each entry is a short,
    human-readable blocker (``"session new"``, ``"no proxy"``, ``"no channels"``).
    """

    ready: bool
    reasons: list[str] = Field(default_factory=list)


class WarmingStateRecord(BaseModel):
    """One row of the ``warming_account_state`` table (no account metadata)."""

    account_id: str = Field(min_length=1)
    state: WarmingState
    cycles_completed: int = Field(default=0, ge=0)
    last_event: str | None = None
    last_cycle_at: str | None = None
    next_run_at: str | None = None
    updated_at: str = Field(min_length=1)
    last_error: str | None = None
    last_action: str | None = None
    last_channel: str | None = None
    heartbeat_at: str | None = None
    started_at: str | None = None
    stopped_at: str | None = None
    flood_wait_seconds: int | None = Field(default=None, ge=0)
    flood_wait_until: str | None = None
    proxy_snapshot: str | None = None
    daily_actions: int = Field(default=0, ge=0)
    daily_count_date: str | None = None
    quarantine_count: int = Field(default=0, ge=0)
    # P1.2: per-loop generation marker. start_warming / reconcile mint a fresh
    # value; the loop captures it and refuses to write through if the DB run_id
    # has changed underneath it (= a newer start replaced this generation).
    run_id: str | None = None


class WarmingStateWrite(BaseModel):
    """Caller-supplied warming-state change; ``core.db`` stamps ``updated_at``."""

    account_id: str = Field(min_length=1)
    state: WarmingState
    cycles_completed: int = Field(default=0, ge=0)
    last_event: str | None = None
    last_cycle_at: str | None = None
    next_run_at: str | None = None
    last_error: str | None = None
    last_action: str | None = None
    last_channel: str | None = None
    heartbeat_at: str | None = None
    started_at: str | None = None
    stopped_at: str | None = None
    flood_wait_seconds: int | None = Field(default=None, ge=0)
    flood_wait_until: str | None = None
    proxy_snapshot: str | None = None
    daily_actions: int = Field(default=0, ge=0)
    daily_count_date: str | None = None
    quarantine_count: int = Field(default=0, ge=0)
    # P1.2: see WarmingStateRecord.run_id.
    run_id: str | None = None
    # P2.4: when True, the upsert sets ``cycles_completed`` to
    # ``warming_account_state.cycles_completed + 1`` on conflict (atomic SQL
    # expression), instead of writing the read-then-compute value the caller
    # supplied in ``cycles_completed``. This closes the lost-update race when
    # two writers concurrently bump the counter.
    increment_cycle: bool = False


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
    last_error: str | None = None
    last_action: str | None = None
    last_channel: str | None = None
    heartbeat_at: str | None = None
    started_at: str | None = None
    stopped_at: str | None = None
    flood_wait_seconds: int | None = Field(default=None, ge=0)
    flood_wait_until: str | None = None
    proxy_snapshot: str | None = None
    daily_actions: int = Field(default=0, ge=0)
    daily_count_date: str | None = None
    quarantine_count: int = Field(default=0, ge=0)
    trust_score: int | None = Field(default=None, ge=0, le=100)
    trust_band: str | None = None
    trust_reasons: list[str] = Field(default_factory=list)
    spam_status: str | None = None
    spam_detail: str | None = None
    age_hours: float | None = Field(default=None, ge=0.0)
    dm_allowed: bool = False
    readiness: WarmingReadiness | None = None


class WarmingSummary(BaseModel):
    """Fleet-level roll-up shown at the top of the warming page."""

    total: int = Field(default=0, ge=0)
    warming: int = Field(default=0, ge=0)
    active: int = Field(default=0, ge=0)
    ready: int = Field(default=0, ge=0)
    attention: int = Field(default=0, ge=0)
    trust_healthy: int = Field(default=0, ge=0)
    trust_watch: int = Field(default=0, ge=0)
    trust_risk: int = Field(default=0, ge=0)


class WarmingBoardState(BaseModel):
    """Everything the warming page renders in one poll tick."""

    idle: list[WarmingAccountState] = Field(default_factory=list)
    warming: list[WarmingAccountState] = Field(default_factory=list)
    channels: WarmingChannelList
    settings: WarmingSettings
    channel_count: int = Field(ge=0)
    active_count: int = Field(ge=0)
    summary: WarmingSummary = Field(default_factory=WarmingSummary)


class StartWarmingRequest(BaseModel):
    account_id: str = Field(min_length=1)


class StopWarmingRequest(BaseModel):
    account_id: str = Field(min_length=1)


class WarmingCycleRequest(BaseModel):
    account_id: str = Field(min_length=1)
    remaining_actions: int | None = None


CycleStatus = Literal["ok", "skipped", "flood_wait", "peer_flood", "error", "failed"]


class WarmingCycleResult(BaseModel):
    """Outcome of one warming cycle — drives counters and the activity log."""

    account_id: str = Field(min_length=1)
    status: CycleStatus
    channels_joined: int = Field(default=0, ge=0)
    channels_read: int = Field(default=0, ge=0)
    reactions_sent: int = Field(default=0, ge=0)
    messages_sent: int = Field(default=0, ge=0)
    detail: str | None = None
    flood_wait_seconds: int | None = Field(default=None, ge=0)
    flood_wait_until: str | None = None
    failures: int = Field(default=0, ge=0)
    attempted_actions: int = Field(default=0, ge=0)
    last_failed_action: str | None = None
    last_failed_channel: str | None = None
