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

from pydantic import BaseModel, ConfigDict, Field

CampaignStatus = Literal["active", "paused", "archived"]
CommentStatus = Literal["claimed", "posted", "failed"]


class CampaignCreate(BaseModel):
    """User input to open a campaign — the product mention lives in the prompt."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    status: CampaignStatus = "active"


class NeurocommentCampaign(BaseModel):
    """One row of ``neurocomment_campaigns``."""

    campaign_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    status: CampaignStatus
    created_at: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)


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


class CampaignChannelList(BaseModel):
    links: list[CampaignChannelLink] = Field(default_factory=list)


class ChannelList(BaseModel):
    """Wrapper for a plain list of channel handles (non-negotiable #2).

    Used by the engine's listener reconcile — the watch set is just the active
    channels, not full link rows.
    """

    channels: list[str] = Field(default_factory=list)


class CampaignAccountLink(BaseModel):
    """One row of ``neurocomment_campaign_accounts`` — an account serving a campaign."""

    campaign_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    created_at: str = Field(min_length=1)


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


class CommentList(BaseModel):
    """Wrapper for a bulk read of comment rows (non-negotiable #2)."""

    comments: list[CommentRecord] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Onboarding (issue #117) — prepare (account, channel) pairs ahead of a post.
# --------------------------------------------------------------------------- #

OnboardingState = Literal[
    "ready",
    "comments_off",
    "join_by_request",
    "captcha_gated",
    "joining",
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


# --------------------------------------------------------------------------- #
# Board read model (issue #119) — bulk-built UI state, no per-card DB queries.
# --------------------------------------------------------------------------- #

ChannelStatus = Literal[
    "ready",
    "comments_off",
    "join_by_request",
    "captcha_gated",
    "throttled",
]


class AccountChannelReadiness(BaseModel):
    """One channel's readiness summary on an account card."""

    channel: str = Field(min_length=1)
    ready: bool
    joined: bool
    captcha_passed: bool


class NeurocommentAccountCard(BaseModel):
    """Per-account card in the work view: limits, health, last activity."""

    account_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    health: str = Field(min_length=1)
    trust_score: int
    trust_band: str = Field(min_length=1)
    spam_status: str | None = None
    comments_last_hour: int
    max_comments_per_hour: int
    comments_today: int
    last_comment_at: str | None = None
    readiness: list[AccountChannelReadiness] = Field(default_factory=list)


class NeurocommentChannelRow(BaseModel):
    """Per-channel row: aggregate status derived from readiness + linked group."""

    channel: str = Field(min_length=1)
    status: ChannelStatus
    ready_accounts: int
    total_accounts: int


class NeurocommentBoard(BaseModel):
    """Bulk read model for the work view of one campaign."""

    campaign_id: str = Field(min_length=1)
    campaign_name: str = Field(min_length=1)
    status: CampaignStatus
    accounts: list[NeurocommentAccountCard] = Field(default_factory=list)
    channels: list[NeurocommentChannelRow] = Field(default_factory=list)
