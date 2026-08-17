"""Pydantic contracts for the neuroshilling domain.

Several Telegram accounts play assigned roles and act out a scripted dialogue in
target chats. This module carries the data contracts flowing between
``core.repositories.neuroshilling`` (persistence), ``services.neuroshilling``
(policy) and ``api.v1.neuroshilling`` (transport). No behaviour, no I/O.

``NeuroshillingRefusalCode`` is the locale-neutral vocabulary of every refusal
this domain can answer with. It is declared whole rather than grown per stage so
that ``tests/test_error_code_i18n_parity.py`` can hold the SPA's ``shell.code.*``
table to it exhaustively — a code that appears later with no copy would
otherwise reach the operator as a raw snake_case token.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

NeuroshillingMode = Literal["campaign", "revive"]
NeuroshillingScenarioStatus = Literal["draft", "approved"]
NeuroshillingRunMode = Literal["sequential", "parallel"]
NeuroshillingAutoresponder = Literal["off", "neurodialog"]
NeuroshillingReplyActivity = Literal["calm", "medium", "active"]
NeuroshillingStatus = Literal["idle", "running", "stopping", "done", "failed"]
NeuroshillingAccountState = Literal["active", "banned", "replaced"]
# Who is holding an account right now, as the accounts modal reports it.
NeuroshillingBusyOwner = Literal["warming", "neuroshilling", "neurocomment"]

NeuroshillingRefusalCode = Literal[
    "campaign_running",
    "too_many_targets",
    "no_targets",
    "not_enough_accounts",
    "account_busy",
    "role_without_account",
    "unknown_role",
    "run_mode_not_supported",
    "scenario_not_approved",
    "scenario_invalid",
    "llm_daily_limit_reached",
    "llm_unavailable",
    "generation_in_progress",
    "target_is_basic_group",
    "media_source_unreachable",
]

_MAX_NAME = 120
_MAX_TOPIC = 2000
# Twenty targets at Telegram's own username ceiling, with room for whole pasted
# links and the separators between them.
_MAX_TARGETS_RAW = 8000
_MAX_MEDIA_LINK = 500
_MAX_SECONDS = 3600
_MAX_LISTEN_MINUTES = 1440
# Posting-rate ceilings. Stage four reads these as the per-account rate, so an
# unbounded value is a ban rather than a big number: neurocomment's tuned figures
# for the same two rules are 10 an hour and 3 per chat a day
# (``core._config_domains``), and these sit a few times above them — room for an
# operator who wants to push, not room for a fleet posting once a second.
_MAX_PER_HOUR = 60
_MAX_PER_CHAT_PER_DAY = 50
# Lifetime ceiling per account for ONE campaign: 100 hours at the hourly cap.
_MAX_TOTAL_PER_ACCOUNT = 1000
# Mirrors ``schemas.neuroshilling_scenario.MAX_STEPS`` by value rather than by
# import: the scenario module imports this one and the dependency may not run
# back the other way. A position past the dialogue is refused at approval anyway;
# this only stops an unbounded integer reaching the column.
_MAX_STEP_POSITION = 50


class NeuroshillingAccountAssignment(BaseModel):
    """One account of a campaign's roster, as the operator arranged it."""

    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(min_length=1)
    # Roles arrive with the scenario, so stage-one rosters are unassigned.
    role_id: str | None = None
    is_reserve: bool = False


class NeuroshillingCampaignAccount(BaseModel):
    """One row of ``neuroshilling_accounts`` as the repository reads it back.

    Separate from :class:`NeuroshillingAccountAssignment` because ``state`` is
    engine-owned (a ban writes it) and must never be settable from a request body.
    """

    account_id: str = Field(min_length=1)
    role_id: str | None = None
    is_reserve: bool = False
    state: NeuroshillingAccountState = "active"


class NeuroshillingCampaignCreate(BaseModel):
    """Opening a campaign asks for a name and nothing else — the rest has defaults."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=_MAX_NAME)
    mode: NeuroshillingMode = "campaign"


class NeuroshillingCampaignUpdate(BaseModel):
    """Whole-form replacement of everything the operator edits on the page.

    Targets and the account roster travel here rather than through endpoints of
    their own: both are edited as part of one card and saving them separately
    would leave windows where the roster references a role the same save removed.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=_MAX_NAME)
    mode: NeuroshillingMode = "campaign"
    topic: str = Field(default="", max_length=_MAX_TOPIC)
    targets_raw: str = Field(default="", max_length=_MAX_TARGETS_RAW)
    unique_messages: bool = True
    use_chat_context: bool = False
    media_message_link: str | None = Field(default=None, max_length=_MAX_MEDIA_LINK)
    media_step_position: int | None = Field(default=None, ge=1, le=_MAX_STEP_POSITION)
    run_mode: NeuroshillingRunMode = "sequential"
    pause_min_seconds: int = Field(default=10, ge=0, le=_MAX_SECONDS)
    pause_max_seconds: int = Field(default=20, ge=0, le=_MAX_SECONDS)
    messages_per_hour: int = Field(default=10, ge=1, le=_MAX_PER_HOUR)
    # 0 = no per-chat ceiling.
    messages_per_chat_per_day: int = Field(default=3, ge=0, le=_MAX_PER_CHAT_PER_DAY)
    # None = no lifetime ceiling for this campaign.
    total_per_account: int | None = Field(default=None, ge=1, le=_MAX_TOTAL_PER_ACCOUNT)
    reserve_enabled: bool = False
    autoresponder: NeuroshillingAutoresponder = "off"
    # Answering real people is OFF unless the operator turns it on: it is the one
    # switch that lets a stranger's text steer what gets published.
    reply_to_humans: bool = False
    reply_activity: NeuroshillingReplyActivity = "medium"
    listen_minutes: int = Field(default=60, ge=1, le=_MAX_LISTEN_MINUTES)
    accounts: list[NeuroshillingAccountAssignment] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_pause_bounds(self) -> NeuroshillingCampaignUpdate:
        if self.pause_min_seconds > self.pause_max_seconds:
            msg = "pause_min_seconds must not exceed pause_max_seconds"
            raise ValueError(msg)
        return self


class NeuroshillingCampaign(BaseModel):
    """One row of ``neuroshilling_campaigns``."""

    campaign_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    mode: NeuroshillingMode
    topic: str = ""
    targets_raw: str = ""
    unique_messages: bool = True
    use_chat_context: bool = False
    media_message_link: str | None = None
    media_step_position: int | None = None
    scenario_status: NeuroshillingScenarioStatus = "draft"
    run_mode: NeuroshillingRunMode = "sequential"
    pause_min_seconds: int = 10
    pause_max_seconds: int = 20
    messages_per_hour: int = 10
    messages_per_chat_per_day: int = 3
    total_per_account: int | None = None
    reserve_enabled: bool = False
    autoresponder: NeuroshillingAutoresponder = "off"
    reply_to_humans: bool = False
    reply_activity: NeuroshillingReplyActivity = "medium"
    listen_minutes: int = 60
    status: NeuroshillingStatus = "idle"
    run_id: str | None = None
    # Exception CLASS NAME, never its text: this field is served back over HTTP.
    last_error: str | None = None
    created_at: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)


class NeuroshillingCampaignList(BaseModel):
    """Wrapper so callers never receive a bare list."""

    campaigns: list[NeuroshillingCampaign] = Field(default_factory=list)


class NeuroshillingBoardAccount(BaseModel):
    """One account of the pool, with whatever this campaign's roster says about it.

    ``assigned`` is what separates a rostered account from a merely offerable one;
    ``role_id``, ``is_reserve`` and ``state`` are meaningful only when it is true.
    ``busy_owner`` is what greys a row out in the picker, and
    ``busy_campaign_name`` is what lets the UI say WHICH campaign holds it rather
    than only that something does.
    """

    account_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    assigned: bool = False
    role_id: str | None = None
    is_reserve: bool = False
    state: NeuroshillingAccountState = "active"
    busy_owner: NeuroshillingBusyOwner | None = None
    busy_campaign_name: str | None = None


class NeuroshillingBoard(BaseModel):
    """One composite read backing the whole page.

    The account pool is ONE list, not a pool plus its rostered subset: the two
    carried the same objects and left the client joining them by id. It also
    carries no derived counters (role/step/account/target counts, dialogue length
    estimates) and no run block: every one of those is an ``arr.length``, a
    ``reduce``, or a field of ``campaign`` already in this same payload, and a
    second copy could only drift from the first.
    """

    campaign: NeuroshillingCampaign
    available: list[NeuroshillingBoardAccount] = Field(default_factory=list)
    # ``targets_raw`` parsed and normalised, in traversal order.
    targets: list[str] = Field(default_factory=list)
