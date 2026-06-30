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

# Five-stage warming lifecycle. Determines per-account daily action cap and
# what behaviour is unlocked. Computed from (calendar age, trust_band) and
# capped from above by trust — see ``services.warming.pacing.effective_phase``.
WarmingPhase = Literal["intro", "settling", "warming", "active", "warmed"]

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
    # Deprecated (audit П2): the fleet-wide override is retired — the engine uses
    # the per-account auto cap (phase + trust) only. Kept so existing rows load;
    # no longer read by the cycle or surfaced in the UI.
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
    """Effective per-cycle intensity for an account, derived from age + trust.

    Produced by the age-based ramp: a fresh account warms quietly (few channels,
    low reaction rate, no DM) and grows to the configured full intensity. The
    daily action cap and lifecycle phase are part of the same derivation —
    one source of truth for "what is this account allowed to do today".
    """

    channels_min: int = Field(ge=1)
    channels_max: int = Field(ge=1)
    reaction_probability: float = Field(ge=0.0, le=1.0)
    dm_allowed: bool
    daily_cap: int = Field(default=0, ge=0)
    phase: WarmingPhase = "intro"
    progress_to_next: float | None = Field(default=None, ge=0.0, le=1.0)
    days_to_next_phase: int | None = Field(default=None, ge=0)


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
    # Persisted lifecycle phase. Compared against the freshly computed phase
    # after each cycle to detect transitions; ``None`` on a brand-new record
    # (seeded on the first cycle, no event fired).
    current_phase: WarmingPhase | None = None
    # ISO timestamp of when the account entered ``current_phase``. Drives the
    # "in phase: N days" hint on the card.
    phase_entered_at: str | None = None
    # Operator graduation flag: True after the warming card's "переместить в
    # нейрокомментинг" button is pressed. The neurocomment warmed-list filters by
    # this so accounts only appear there after an explicit hand-off, not when they
    # silently cross ``warmed_min_days``.
    promoted_to_nc: bool = False


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
    # Round 2 P1: optional CAS gate. When set, the UPDATE branch of the upsert
    # only fires if the row's current ``run_id`` matches this value. A stale
    # loop whose run_id was replaced by a newer start_warming therefore turns
    # into a silent no-op instead of overwriting the new generation. Carries
    # no effect on the INSERT branch (a new row has no run_id to mismatch).
    expected_run_id: str | None = None
    # See WarmingStateRecord — persisted lifecycle phase + entry timestamp.
    current_phase: WarmingPhase | None = None
    phase_entered_at: str | None = None


class WarmingStateWriteResult(BaseModel):
    """Outcome of an upsert against ``warming_account_state`` (Round-4 P1.2).

    ``record`` is always the post-upsert row (a fresh readback). ``applied``
    distinguishes:
    - True  → the INSERT happened, or the ON CONFLICT DO UPDATE's WHERE clause
              matched and the UPDATE went through.
    - False → the CAS predicate (``expected_run_id`` + ``state != 'idle'``)
              ruled out the UPDATE; the row is whatever the conflicting
              generation wrote, NOT what this caller asked for.

    Callers that supplied an ``expected_run_id`` use ``applied`` to decide
    whether to keep going (continue the cycle) or bail (a newer generation
    has taken over).
    """

    record: WarmingStateRecord
    applied: bool


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
    # ISO-3166 alpha-2 country pair surfaced on the card so an operator can see
    # *why* a "geo mismatch" trust reason was raised. Both stay None when the
    # phone can't be parsed or the proxy has no country code.
    phone_country: str | None = None
    proxy_country: str | None = None
    # Account phone + assigned-proxy type, surfaced so the ready/warmed cards show
    # the real flag + proxy badge (was a design-first hash mock). Both None when
    # the account has no phone / no proxy assigned.
    phone: str | None = None
    # Mirrors ``AccountRead.proxy_type`` (a free ``str`` code, not the ProxyType
    # literal) so the assignment in ``load_board`` type-checks.
    proxy_type: str | None = None
    # Lifecycle phase + display affordances, derived per card from age + trust.
    phase: WarmingPhase | None = None
    phase_label: str | None = None
    daily_cap: int = Field(default=0, ge=0)
    progress_to_next: float | None = Field(default=None, ge=0.0, le=1.0)
    days_to_next_phase: int | None = Field(default=None, ge=0)
    # Whole days since warming was first started for this account (from
    # ``WarmingStateRecord.started_at``). ``None`` when warming never ran.
    warming_days: int | None = Field(default=None, ge=0)
    readiness: WarmingReadiness | None = None
    # Operator-set: account has been graduated to the neurocomment pool. Drives
    # the "переместить в нейрокомментинг" button on the card (hidden once True).
    promoted_to_nc: bool = False


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


class WarmedAccount(BaseModel):
    """A sufficiently-warmed account, for the neurocomment page's overview field."""

    account_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    warming_days: int = Field(ge=0)


class WarmedAccountList(BaseModel):
    """Wrapper for a bulk read of warmed accounts (non-negotiable #2)."""

    accounts: list[WarmedAccount] = Field(default_factory=list)


class StartWarmingRequest(BaseModel):
    account_id: str = Field(min_length=1)


class StopWarmingRequest(BaseModel):
    account_id: str = Field(min_length=1)


class WarmingCycleRequest(BaseModel):
    account_id: str = Field(min_length=1)
    remaining_actions: int | None = None
    # Trust+readiness-aware DM permission computed by the loop (П11). ``None``
    # means "decide from age alone" so direct callers keep the old behaviour.
    dm_allowed: bool | None = None


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
