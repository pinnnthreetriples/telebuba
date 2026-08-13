"""Pydantic schemas for the neurocomment domain (issue #114).

Data contracts flowing between ``core.repositories.neurocomment`` (persistence),
the future ``services/neurocomment/`` (business logic) and features (UI). No
behaviour, no I/O — non-negotiable #2. Style mirrors ``schemas/warming.py``.

Campaign lifecycle (``CampaignStatus``):
- ``active``   — running; its channels hold the "one active campaign" slot.
- ``paused``   — temporarily off; channels freed (links deactivated).
- ``archived`` — retired.

Comment lifecycle (``CommentStatus``):
- ``waiting`` — the post is parked: the claim is won but held, waiting for a human
  comment to reply to. Resolved by the sweep, which promotes it to ``claimed``.
- ``claimed`` — a fleet account won the ``(channel, post_id)`` claim, not yet posted.
- ``posted``  — comment delivered (``comment_msg_id`` set).
- ``failed``  — delivery failed after retries.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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

# The operator-editable settings pair moved out for the same budget, the same way.
from schemas._neurocomment_settings import (  # noqa: F401 - re-export for existing call sites
    CommentMode,
    NeurocommentSettings,
    NeurocommentSettingsUpdate,
)

CampaignStatus = Literal["active", "paused", "archived"]
CommentStatus = Literal["waiting", "claimed", "posted", "failed"]


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
    # When the listener last saw this channel publish (ISO-8601 UTC). ``None`` = not since
    # the column existed, which the inactive-channel rule reads as "age it from
    # ``created_at``" rather than as silence.
    last_post_at: str | None = None


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
    # The spent budget has already been reported: the pair left the chat, the operator has
    # its log line and the board its badge. Only a "said it once" mark — the rule reads the
    # counter above, not this — because the review that writes it runs every five minutes.
    rejoin_gave_up: bool = False
    # The Telegram verdict that took this pair out of the chat — the error class itself,
    # so the rule can tell a kick (retryable) from a dead address (not). None = unknown,
    # which every rule reads as retryable.
    access_lost_reason: str | None = None
    # The guardian-bot captcha the solver could not pass (#49). ``captcha_retry_at`` is
    # when the sweep authorised this pair's ONE re-solve (None = not asked yet), and
    # ``captcha_gave_up`` is the terminal verdict: the pair stopped trying and left the
    # chat, so nothing re-joins or re-solves for it again.
    captcha_retry_at: str | None = None
    captcha_gave_up: bool = False


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

# The board read model (``ChannelStatus`` + the four board classes) lives in
# ``schemas.neurocomment_board`` (file-size cap), exactly like the onboarding-progress
# schemas above: it imports ``CampaignStatus`` / ``CommentRecord`` from here, so this
# module must not import it back — callers import the board names from there.


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
