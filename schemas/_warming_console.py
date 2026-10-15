"""Operator-console fields of the warming card and row (Прогрев 2.0, stage 2).

Split from ``schemas.warming`` for the file-size budget: ``WarmingStateRecord`` and
``WarmingAccountState`` inherit the two mixins below, so their fields read like any
other on those models. Data contract only.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from schemas._warming_visits import VisitKind, VisitStep  # noqa: TC001 - pydantic resolves them.
from schemas.geo import GeoMatchStatus  # noqa: TC001 - pydantic resolves it at runtime.
from schemas.survival import AccountSurvival  # noqa: TC001 - pydantic resolves it at runtime.

# How an account enters neurocomment: ``soft`` caps it lower for its first days there.
NcStartMode = Literal["soft", "normal"]

# Why inter-account DMs are (not) open for one card, first blocker wins:
# chat switched off · no text-LLM key · the account's own spam block (until lifted) · fleet
# pause · readiness · warming too short · trust band or a post-quarantine block · open.
DmStatus = Literal[
    "on", "chat_off", "no_llm_key", "spam_blocked", "paused", "not_ready", "too_young", "trust"
]

# The one problem a card names, most urgent first; the SPA translates the code.
AttentionCode = Literal[
    "spam_blocked",
    "quarantine",
    "flood_wait",
    "stopping",
    "quarantine_unreadable",
    "not_ready",
    "error",
]


class WarmingAttention(BaseModel):
    """What needs the operator on one card, as a code instead of prose."""

    code: AttentionCode
    # When it ends on its own (quarantine re-check, flood deadline); ``None`` = it won't.
    until: str | None = None
    # Quarantine: which re-check this is, of how many before warming gives up.
    attempt: int | None = Field(default=None, ge=1)
    attempts: int | None = Field(default=None, ge=1)
    # ``not_ready``: the readiness reasons, same vocabulary as ``WarmingReadiness``.
    reasons: list[str] = Field(default_factory=list)


class WarmingStateMarks(BaseModel):
    """Row columns written outside the main upsert (migrations 82-84, 94)."""

    # Geo consent as "PHONE>PROXY" ("MX>US"): it covers only that pair, so a new proxy
    # country asks again without anything having to clear it.
    geo_ack: str | None = None
    # Today's settled actions when the cycle in flight booked its budget, so the card
    # shows what was spent rather than the whole reservation.
    cycle_base_actions: int | None = Field(default=None, ge=0)
    nc_start_mode: NcStartMode | None = None
    nc_handoff_at: str | None = None
    # The visit the booked cycle runs and its step order (4.4, migration 87).
    visit_kind: VisitKind | None = None
    cycle_plan: list[VisitStep] = Field(default_factory=list)
    # The account's warming wave (migration 94): 1 for its first stint, +1 for every
    # fresh stint of the same account life («Вернуть в прогрев» included). A deletion
    # takes the row with it, so a re-imported account starts again at 1.
    wave: int = Field(default=1, ge=1)
    # When @SpamBot confirmed a spam block on the quarantine re-check. Permanent for the
    # account life: DMs stay shut, ``limited`` no longer quarantines or refuses, and the
    # card wears a «Спамблок» badge after the operator chose to keep the account.
    spam_blocked_at: str | None = None


class WarmingCardConsole(BaseModel):
    """The new card rows: settled actions, DM status, one attention item, geo verdict."""

    daily_settled: int = Field(default=0, ge=0)
    dm_status: DmStatus = "on"
    attention: WarmingAttention | None = None
    geo_status: GeoMatchStatus = "unknown"
    # The operator accepted this exact phone/proxy country pair at a start.
    geo_accepted: bool = False
    # Days since release and whether the account is alive (stage 3); ``None`` = not released.
    survival: AccountSurvival | None = None
    # The current (or last) visit and its steps; the card's progress follows this order.
    visit_kind: VisitKind | None = None
    cycle_plan: list[VisitStep] = Field(default_factory=list)
    # The stint's wave (the card names it from wave 2 on) and the spam-block mark, which a
    # clean @SpamBot answer clears.
    wave: int = Field(default=1, ge=1)
    spam_blocked_at: str | None = None


class SoftStartProgress(BaseModel):
    """Where a soft neurocomment start is: day ``soft_start_day`` of ``soft_start_days``."""

    soft_start_day: int | None = Field(default=None, ge=1)
    soft_start_days: int = Field(default=0, ge=0)


class SoftStartPlan(BaseModel):
    """The soft start's numbers (``settings.soft_start``), quoted by the hand-off dialog."""

    days: int = Field(default=5, ge=1)
    early_days: int = Field(default=2, ge=0)
    early_comments_per_hour: int = Field(default=2, ge=1)
    early_comments_per_channel_per_day: int = Field(default=1, ge=1)
    early_joins_per_day: int = Field(default=5, ge=1)
    late_comments_per_hour: int = Field(default=4, ge=1)
    late_comments_per_channel_per_day: int = Field(default=2, ge=1)
    late_joins_per_day: int = Field(default=10, ge=1)
    first_day_comments: int = Field(default=5, ge=1)
    last_day_comments: int = Field(default=15, ge=1)


class HandoffRequest(BaseModel):
    """Body of ``POST /warming/handoff``: the account and how it enters neurocomment."""

    account_id: str = Field(min_length=1)
    # The dialog preselects the soft start; «Как обычно» sends ``normal``.
    start_mode: NcStartMode = "soft"
