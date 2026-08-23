"""Board read model for the neurocomment work view (issue #119).

Bulk-built UI state — one read per campaign, no per-card DB queries. Split out of
``schemas.neurocomment`` at the file-size cap, the way ``schemas.neurocomment_progress``
was: it imports ``CampaignStatus`` / ``CommentRecord`` from there, so that module must not
import this one back — and does not re-export these names either. Callers import the board
model from here.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from schemas.neurocomment import (  # noqa: TC001 - Pydantic needs the runtime types.
    CampaignStatus,
    CommentRecord,
)

ChannelStatus = Literal[
    "ready",
    "comments_off",
    "join_by_request",
    "join_failed",
    # Kicked out of the chat and walking itself back in (``_rejoin``): the same row
    # shape as ``join_failed``, but with re-join attempts still to spend.
    "rejoining",
    # Account-card only: THIS account spent its re-join budget on the channel and left
    # its chat. Never an aggregate — the channel keeps whatever its other accounts make
    # of it, which is the whole point of showing this per account.
    "rejoin_exhausted",
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
    # This account spent its whole re-join budget here and left the chat. Same reason as
    # ``banned`` above, and the same shape of remedy.
    rejoin_gave_up: bool = False


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
    # The same deletions split by the channel they happened in. The board row names ONE
    # channel per account, so the chip beside it has to mean "this account, that channel"
    # — the flat total above put a deletion from another channel next to whichever channel
    # the row happened to be showing. Absent keys are zero; the flat total stays because
    # the «Удалено» tile sums whole accounts, not pairs.
    deleted_by_channel: dict[str, int] = Field(default_factory=dict)
    last_comment_at: str | None = None
    # Text of the most recent posted comment (None until the account comments, or
    # when the stored row has no text). Surfaces the real comment in the board.
    last_comment_text: str | None = None
    # Where that same comment went. Rides the card rather than being looked up in
    # ``NeurocommentBoard.comments``, which is a newest-first prefix capped at
    # ``board_comment_feed_limit`` (50) across the WHOLE campaign: six accounts under the
    # default hourly cap outrun it in under an hour, and the board would then pair this
    # account's real ``last_comment_text`` with some other channel it merely joined.
    last_comment_channel: str | None = None
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
