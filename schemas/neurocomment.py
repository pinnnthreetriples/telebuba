"""Pydantic schemas for the neurocomment domain (issue #114).

Data contracts flowing between ``core.repositories.neurocomment`` (persistence),
the future ``services/neurocomment/`` (business logic) and features (UI). No
behaviour, no I/O — non-negotiable #2. Style mirrors ``schemas/warming.py``.

Campaign lifecycle (``CampaignStatus``):
- ``active``   — running; its channels hold the "one active campaign" slot.
- ``paused``   — temporarily off; channels freed (links deactivated).
- ``archived`` — retired.

Comment lifecycle (``CommentStatus``):
- ``claimed`` — a fleet account won the ``(channel, post_id)`` claim, not yet posted.
- ``posted``  — comment delivered (``comment_msg_id`` set).
- ``failed``  — delivery failed after retries.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# The inbound request bodies live in a sibling module for the file-size budget;
# re-exported here so ``from schemas.neurocomment import LinkChannelRequest`` etc.
# keep working.
from schemas._neurocomment_requests import (  # noqa: F401 - re-export for existing call sites
    AssignAccountRequest,
    CampaignRunStatus,
    LinkChannelRequest,
    RetryPairRequest,
    SetAccountChannelRequest,
    SetCampaignStatusRequest,
    SolverToggleRequest,
    StartNeurocommentRequest,
    UpdatePromptRequest,
)

CampaignStatus = Literal["active", "paused", "archived"]
CommentStatus = Literal["claimed", "posted", "failed"]


class CampaignCreate(BaseModel):
    """User input to open a campaign — the product mention lives in the prompt."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    # Generous product-mention prompt ceiling — bounds what gets re-sent to the LLM
    # on every comment generation.
    prompt: str = Field(min_length=1, max_length=4000)
    status: CampaignStatus = "active"


class NeurocommentCampaign(BaseModel):
    """One row of ``neurocomment_campaigns``.

    ``channel_count`` / ``account_count`` are populated on the campaigns-list payload
    so every card (not just the selected one) can show real link counts; they are 0
    on a bare row read (``fetch_campaign``).
    """

    campaign_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    status: CampaignStatus
    created_at: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)
    # Per-campaign challenge-solver override (#148): None defers to the global flag.
    solver_enabled: bool | None = None
    channel_count: int = Field(default=0, ge=0)
    account_count: int = Field(default=0, ge=0)


class CampaignList(BaseModel):
    """Wrapper so callers never receive a raw list (non-negotiable #2)."""

    campaigns: list[NeurocommentCampaign] = Field(default_factory=list)


class CampaignChannelLink(BaseModel):
    """One row of ``neurocomment_campaign_channels`` — a channel bound to a campaign."""

    id: int
    campaign_id: str = Field(min_length=1)
    channel: str = Field(min_length=1)
    active: bool
    created_at: str = Field(min_length=1)
    # "This channel will not let us write" (#147): completed pause rounds, and the
    # ISO-8601 UTC instant the current pause ends (``None`` = not paused). Carried on the
    # link so the board and the onboarding loop get the pause state out of a read they
    # already make, instead of one query per channel.
    pause_rounds: int = 0
    paused_until: str | None = None


class CampaignChannelList(BaseModel):
    links: list[CampaignChannelLink] = Field(default_factory=list)


class ChannelPauseState(BaseModel):
    """What one channel link looks like after a round of the pause rule just ended.

    ``campaign_id`` is what ``deactivate_channel`` needs when the last round runs out —
    the caller holds the channel handle already, so the model does not repeat it.
    """

    campaign_id: str = Field(min_length=1)
    pause_rounds: int
    paused_until: str


ChannelLinkStatus = Literal["linked", "already_assigned"]


class ChannelLinkOutcome(BaseModel):
    """Result of attaching a channel to a campaign.

    ``already_assigned`` means the channel is the active target of another campaign
    (the repository's uniqueness guard). The service returns this instead of letting
    ``ChannelAlreadyAssignedError`` reach the UI, so features never catch internals (#2).
    """

    status: ChannelLinkStatus
    channel: str = Field(min_length=1)


class ChannelList(BaseModel):
    """Wrapper for a plain list of channel handles (non-negotiable #2).

    Used by the engine's listener reconcile — the watch set is just the active
    channels, not full link rows.
    """

    channels: list[str] = Field(default_factory=list)


class CampaignAccountLink(BaseModel):
    """One row of ``neurocomment_campaign_accounts`` — an account serving a campaign.

    ``channels`` is the subset of campaign channels the account targets: when
    non-empty, the account onboards + comments ONLY on those channels; an empty
    list (the default) keeps the all-channels behaviour.
    """

    campaign_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    created_at: str = Field(min_length=1)
    channels: list[str] = Field(default_factory=list)


class CampaignAccountList(BaseModel):
    links: list[CampaignAccountLink] = Field(default_factory=list)


class LinkedDiscussionGroup(BaseModel):
    """Cached resolution of a channel's linked discussion group.

    ``linked_chat_id`` is ``None`` and ``comments_enabled`` is ``False`` when the
    channel has comments switched off (no discussion group).
    """

    channel: str = Field(min_length=1)
    linked_chat_id: int | None = None
    comments_enabled: bool
    checked_at: str = Field(min_length=1)


class LinkedGroupList(BaseModel):
    """Wrapper for a bulk read of linked-group resolutions (non-negotiable #2)."""

    groups: list[LinkedDiscussionGroup] = Field(default_factory=list)


class NeurocommentReadiness(BaseModel):
    """Per-(account, channel) readiness to comment: joined + captcha passed."""

    account_id: str = Field(min_length=1)
    channel: str = Field(min_length=1)
    joined: bool
    captcha_passed: bool
    ready: bool
    checked_at: str = Field(min_length=1)
    # Operator skip (#148); auto-ban (#30). Both make the engine never select the pair.
    human_skipped: bool = False
    banned: bool = False
    # Approval-gated join request: when the most recent one was sent (None = none
    # outstanding) and how many have gone out. Drives the 24h retry / 48h channel drop.
    join_requested_at: str | None = None
    join_request_attempts: int = 0
    # Automatic re-join after the pair lost access to the chat: when the most recent
    # attempt went out (None = none yet) and how many have. Drives the daily retry /
    # the channel drop once every account has used its attempts.
    rejoin_attempted_at: str | None = None
    rejoin_attempts: int = 0
    # The Telegram verdict that took this pair out of the chat — the error class itself,
    # so the rule can tell a kick (retryable) from a dead address (not). None = unknown,
    # which every rule reads as retryable.
    access_lost_reason: str | None = None


class ReadinessList(BaseModel):
    """Wrapper for a bulk read of readiness rows (non-negotiable #2)."""

    readiness: list[NeurocommentReadiness] = Field(default_factory=list)


class CommentRecord(BaseModel):
    """One row of ``neurocomment_comments`` — the claim + outcome for a post."""

    channel: str = Field(min_length=1)
    post_id: int
    campaign_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    status: CommentStatus
    comment_text: str | None = None
    comment_msg_id: int | None = None
    created_at: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)
    # Set when a delivered comment is later found removed from the channel; NULL = live.
    deleted_at: str | None = None


class CommentList(BaseModel):
    """Wrapper for a bulk read of comment rows (non-negotiable #2)."""

    comments: list[CommentRecord] = Field(default_factory=list)


class AccountCommentCount(BaseModel):
    """One account's comment count within a quota window (bulk quota read)."""

    account_id: str = Field(min_length=1)
    count: int


class CommentCountList(BaseModel):
    """Wrapper for bulk per-account comment counts (non-negotiable #2).

    Lets account selection score N candidates' quota usage from one grouped query
    instead of one count per candidate.
    """

    counts: list[AccountCommentCount] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Onboarding (issue #117) — prepare (account, channel) pairs ahead of a post.
# --------------------------------------------------------------------------- #

OnboardingState = Literal[
    "ready",
    "comments_off",
    "join_by_request",
    "chat_restricted",
    "bot_challenge",
    # The CHANNEL is paused because it will not let us write (a lost captcha or a write
    # gate) — not a property of this account, which is why it is not "bot_challenge_*".
    "channel_paused",
    "joining",
    "human_skipped",
    "banned",
    "failed",
]


class AccountChannelOnboarding(BaseModel):
    """Outcome of preparing one account to comment on one channel.

    ``reason`` carries a short human note for the non-``ready`` states (the
    flood-wait detail, the failing error type, etc.).
    """

    account_id: str = Field(min_length=1)
    channel: str = Field(min_length=1)
    state: OnboardingState
    reason: str | None = None


class CampaignOnboardingResult(BaseModel):
    """Per-campaign roll-up of every (account, channel) onboarding outcome."""

    campaign_id: str = Field(min_length=1)
    outcomes: list[AccountChannelOnboarding] = Field(default_factory=list)


# The onboarding-progress schemas (``OnboardingProgressCode`` / ``OnboardingProgressEvent``)
# live in ``schemas.neurocomment_progress`` (file-size cap); they import ``OnboardingState``
# from here, so this module must not import them back.

# --------------------------------------------------------------------------- #
# Board read model (issue #119) — bulk-built UI state, no per-card DB queries.
# --------------------------------------------------------------------------- #

ChannelStatus = Literal[
    "ready",
    "comments_off",
    "join_by_request",
    "join_failed",
    # Kicked out of the chat and walking itself back in (``_rejoin``): the same row
    # shape as ``join_failed``, but with re-join attempts still to spend.
    "rejoining",
    "chat_restricted",
    "banned",  # no account ready here and at least one auto-banned (#30)
    "bot_challenge",
    # Paused: the channel refuses our writes and is serving out one of its rounds (#147).
    "channel_paused",
    "throttled",
    # no readiness rows yet — onboarding hasn't produced data for this channel
    "no_data",
]


class AccountChannelReadiness(BaseModel):
    """One channel's readiness summary on an account card."""

    channel: str = Field(min_length=1)
    ready: bool
    joined: bool
    captcha_passed: bool
    human_skipped: bool = False
    # Permanent per-pair ban (#30). Carried per (account, channel) because the channel
    # row hides it: one banned account among five ready ones still reports ``ready``,
    # and the only remedy — add another account — needs to know WHO is burnt WHERE.
    banned: bool = False


class NeurocommentAccountCard(BaseModel):
    """Per-account card in the work view: limits and last activity.

    Carries no trust/health/spam: the SPA reads those from ``AccountRead`` on the
    accounts/warming surfaces, so deriving them per board poll was pure waste.
    """

    account_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    comments_last_hour: int
    # The cap the engine enforces (saved ``neurocomment_settings`` row, #19) — not
    # the config default, or the card would render a denominator nobody honours.
    max_comments_per_hour: int
    comments_today: int
    # How many of those ``comments_today`` the sweep later found gone. Counted off the
    # same rows, so the board's "deleted" total can never exceed its "comments" total —
    # a per-channel sum could, because a channel row disappears when the operator
    # unlinks it while the comments it hosted stay on the account.
    deleted_today: int = 0
    last_comment_at: str | None = None
    # Text of the most recent posted comment (None until the account comments, or
    # when the stored row has no text). Surfaces the real comment in the board.
    last_comment_text: str | None = None
    # Campaign channels this account targets (comments only there); empty = all.
    pinned_channels: list[str] = Field(default_factory=list)
    readiness: list[AccountChannelReadiness] = Field(default_factory=list)


class NeurocommentChannelRow(BaseModel):
    """Per-channel row: aggregate status derived from readiness + linked group."""

    channel: str = Field(min_length=1)
    status: ChannelStatus
    ready_accounts: int
    total_accounts: int
    # Comments of ours removed from this channel within the board's 24h window.
    deleted_recent: int = 0


class NeurocommentBoard(BaseModel):
    """Bulk read model for the work view of one campaign."""

    campaign_id: str = Field(min_length=1)
    campaign_name: str = Field(min_length=1)
    status: CampaignStatus
    solver_enabled: bool | None = None  # per-campaign solver override (#148)
    accounts: list[NeurocommentAccountCard] = Field(default_factory=list)
    channels: list[NeurocommentChannelRow] = Field(default_factory=list)
    # Published-comments feed: the campaign's recent posted comments, newest first,
    # capped by ``settings.neurocomment.board_comment_feed_limit``. Lets the UI show
    # every published comment instead of only each account's last one.
    comments: list[CommentRecord] = Field(default_factory=list)


class NeurocommentRuntimeStatus(BaseModel):
    """Fleet-wide runtime state for the page's running indicator + live animation.

    ``running`` is the single source of truth the UI animates on: it reflects the
    persisted ``listener_running`` flag (the engine is actively subscribed), NOT
    merely whether an account is remembered. ``listener_account_id`` is the
    *remembered* listener and is returned even when ``running`` is False — that is a
    PAUSED runtime, and the SPA keeps the listener strip visible (distinct from "no
    listener", where the field is null). ``active_channels`` is how many channels the
    listener is actually watching (populated only while running), i.e. the watch set
    across all active campaigns minus ``unwatched_channels``.
    ``log_limit`` is the operator-configured activity-log row cap the SPA reads
    instead of hardcoding one (from ``settings.neurocomment.log_limit``).
    """

    running: bool
    active_channels: int = 0
    # Channels in the active watch set the listener could not resolve, so no post from
    # them reaches the engine. Sorted; empty when the whole watch set is live. The board
    # still renders such a channel `ready`, so the SPA warns off this list.
    unwatched_channels: list[str] = Field(default_factory=list)
    listener_account_id: str | None = None
    log_limit: int = Field(ge=1)
    # True while the background campaign-onboarding pass is in flight (accounts are
    # actively joining channels). The SPA animates the board on this so a slow,
    # jittered onboarding reads as "working", not "no data".
    onboarding: bool = False


class NeurocommentSettings(BaseModel):
    """Operator-editable neurocomment limits — the engine reads these at selection."""

    max_comments_per_hour: int = Field(ge=1)
    max_comments_per_channel_per_day: int = Field(ge=0)
    reply_delay_min_seconds: float = Field(ge=0)
    reply_delay_max_seconds: float = Field(ge=0)
    min_trust_score: int = Field(ge=0, le=100)
    updated_at: str = Field(min_length=1)


class NeurocommentSettingsUpdate(BaseModel):
    """Caller-supplied neurocomment-settings change from the Settings screen."""

    model_config = ConfigDict(extra="forbid")

    max_comments_per_hour: int = Field(ge=1)
    max_comments_per_channel_per_day: int = Field(ge=0)
    reply_delay_min_seconds: float = Field(ge=0)
    # Deliberately unbounded above: the delay is spent INSIDE a claim, and that is handled
    # where the waiting happens — ``_generate._sleep_beating`` spends it in slices with a
    # claim heartbeat between them, so no length outlives ``stale_claim_reclaim_seconds``.
    # A cap here would instead lock the whole Settings form: it seeds from the unbounded
    # read model, the client schema allows 3600, and every save resends the full object, so
    # one legally stored value above the cap would 422 every unrelated edit, no field marked.
    reply_delay_max_seconds: float = Field(ge=0)
    min_trust_score: int = Field(ge=0, le=100)

    @model_validator(mode="after")
    def _check_delay_bounds(self) -> NeurocommentSettingsUpdate:
        if self.reply_delay_min_seconds > self.reply_delay_max_seconds:
            msg = "reply_delay_min_seconds must not exceed reply_delay_max_seconds"
            raise ValueError(msg)
        return self
