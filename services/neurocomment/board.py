"""Neurocomment work-view read model (issue #119).

Builds the board the UI polls for one campaign: one card per serving account
(quota usage, last comment) plus one row per watched channel (aggregate
status). Every DB row is bulk-loaded once here — no per-card N+1, mirroring
``services.warming.board.load_board``.

The only neurocomment-specific logic is the per-channel status derivation; trust
and warming readiness are the accounts/warming surfaces' business, not the
board's (the SPA reads them from ``AccountRead`` there).
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, NamedTuple

from core.config import settings
from core.db import (
    fetch_campaign,
    list_accounts_by_ids,
    list_campaign_accounts,
    list_campaign_channels,
    list_campaign_readiness,
    list_challenged_channels,
    list_delivered_comments_since,
    list_linked_groups,
    list_posted_comments_since,
    list_waiting_comments,
    load_account_limit_overrides,
)
from schemas.neurocomment_board import (
    AccountChannelReadiness,
    ChannelStatus,
    NeurocommentAccountCard,
    NeurocommentBoard,
    NeurocommentChannelRow,
)
from services._account_limits import resolve_limits
from services.neurocomment import _pair_status, _state, settings_store

if TYPE_CHECKING:
    from schemas.accounts import AccountRead
    from schemas.neurocomment import (
        CommentRecord,
        LinkedDiscussionGroup,
        NeurocommentReadiness,
    )


class _AccountSignals(NamedTuple):
    """The bulk-loaded per-account signals that travel together into a card."""

    account: AccountRead
    pinned_channels: list[str]  # channel subset, or empty when the account serves all
    parked: list[CommentRecord]  # posts held for a human comment — quota is already spent


class _ChannelFlags(NamedTuple):
    """Per-channel signals that travel together into a channel row."""

    challenged: bool
    paused: bool  # serving out a round of the "will not let us write" rule (#147)
    deleted_recent: int  # our comments removed from this channel in the 24h window


async def load_neurocomment_board(campaign_id: str) -> NeurocommentBoard | None:
    """Assemble the work-view board for one campaign, or ``None`` if it is gone."""
    campaign = await fetch_campaign(campaign_id)
    if campaign is None:
        return None

    account_links = (await list_campaign_accounts(campaign_id)).links
    account_ids = [link.account_id for link in account_links]
    pins = {link.account_id: link.channels for link in account_links}
    # The links, not just their handles: each one carries its own pause deadline (#147),
    # so the board reads every channel's pause out of this one query instead of one
    # lookup per rendered row.
    channel_links = (await list_campaign_channels(campaign_id)).links
    channels = [link.channel for link in channel_links]

    accounts = {acc.account_id: acc for acc in (await list_accounts_by_ids(account_ids)).accounts}
    readiness = (await list_campaign_readiness(campaign_id)).readiness
    linked = {g.channel: g for g in (await list_linked_groups(channels)).groups}
    challenged = set((await list_challenged_channels(channels)).channels)
    # The card's quota denominator must be the cap the engine actually enforces —
    # the operator-editable saved row (#19), not the .env default. One bulk read
    # here, threaded into every card, so it stays off the per-card path.
    limits = await settings_store.load_settings()
    # …and per account, since #58: an operator who raised one account's hourly cap must
    # see that account's card counting against ITS number, not the fleet's.
    overrides = await load_account_limit_overrides(account_ids)

    now = datetime.now(UTC)
    day_ago = (now - timedelta(days=1)).isoformat()
    posted = (await list_posted_comments_since(campaign_id, day_ago)).comments
    # The quota spends a slot the moment a post is PARKED, not when it is published, so a
    # card counting only ``posted`` against the hourly cap reported free capacity while
    # selection was already answering ``no_account_available`` — the board contradicting the
    # engine. One fleet-wide read (nothing else revisits parked rows, so the reader is
    # unscoped), narrowed to this campaign here and to the hour window per card.
    parked = [c for c in (await list_waiting_comments()).comments if c.campaign_id == campaign_id]

    cards = [
        _build_card(
            signals=_AccountSignals(
                account=accounts[account_id],
                pinned_channels=pins.get(account_id, []),
                parked=[c for c in parked if c.account_id == account_id],
            ),
            readiness=[r for r in readiness if r.account_id == account_id],
            posted=[c for c in posted if c.account_id == account_id],
            max_comments_per_hour=resolve_limits(
                overrides.get(account_id), limits
            ).max_comments_per_hour,
            now=now,
        )
        for account_id in account_ids
        if account_id in accounts
    ]
    # The DELIVERED set, not ``posted``: the sweep stamps ``deleted_at`` on any row carrying
    # a message id, so a comment mis-classified ``failed`` (its claim reclaimed mid-send)
    # can trip the channel back-off while contributing nothing to the number meant to
    # explain it — an unexplained back-off on the operator's board. The cards above keep
    # counting only ``posted``, which is what "comments this account published" means.
    delivered = (await list_delivered_comments_since(campaign_id, day_ago)).comments
    delivered_deleted = Counter(c.channel for c in delivered if c.deleted_at)
    rows = [
        _build_channel_row(
            link.channel,
            readiness,
            linked.get(link.channel),
            _ChannelFlags(
                challenged=link.channel in challenged,
                paused=_state.channel_paused(link.paused_until, now),
                deleted_recent=delivered_deleted[link.channel],
            ),
        )
        for link in channel_links
    ]
    feed = sorted(posted, key=lambda c: c.created_at, reverse=True)[
        : settings.neurocomment.board_comment_feed_limit
    ]
    return NeurocommentBoard(
        campaign_id=campaign.campaign_id,
        campaign_name=campaign.name,
        status=campaign.status,
        solver_enabled=campaign.solver_enabled,
        accounts=cards,
        channels=rows,
        comments=feed,
    )


def _build_card(
    *,
    signals: _AccountSignals,
    readiness: list[NeurocommentReadiness],
    posted: list[CommentRecord],
    max_comments_per_hour: int,
    now: datetime,
) -> NeurocommentAccountCard:
    account, pinned_channels, parked = signals
    hour_ago = (now - timedelta(hours=1)).isoformat()
    # Parked posts count here and NOWHERE else on the card: this is the only number read
    # against the engine's hourly cap, while ``comments_today`` / ``deleted_today`` answer
    # "what did this account publish", which a post still waiting has not done.
    last_hour = sum(1 for c in (*posted, *parked) if c.created_at >= hour_ago)
    latest = max(posted, key=lambda c: c.created_at, default=None)
    posted_deleted = Counter(c.channel for c in posted if c.deleted_at)
    return NeurocommentAccountCard(
        account_id=account.account_id,
        label=account.label or account.account_id,
        comments_last_hour=last_hour,
        max_comments_per_hour=max_comments_per_hour,
        comments_today=len(posted),
        deleted_today=sum(posted_deleted.values()),
        last_comment_at=latest.created_at if latest else None,
        last_comment_text=latest.comment_text if latest else None,
        last_comment_deleted=bool(latest and latest.deleted_at),
        # Same row as the text above, so the board's channel and comment columns can
        # never name two different events.
        last_comment_channel=latest.channel if latest else None,
        pinned_channels=pinned_channels,
        readiness=[
            AccountChannelReadiness(
                channel=r.channel,
                ready=r.ready,
                joined=r.joined,
                captcha_passed=r.captcha_passed,
                human_skipped=r.human_skipped,
                # Every "this pair is out of service" flag travels to the card: the
                # channel row aggregates them away (a channel with one ready account
                # reads ``ready``), so the card is the only surface that can say which
                # account is banned (#30), skipped (#148), or done re-joining here.
                banned=r.banned,
                rejoin_gave_up=r.rejoin_gave_up,
                # Same `posted` rows as ``deleted_today`` above, split by channel: the
                # board row's chip sits beside ONE channel name and has to mean this pair.
                deleted=posted_deleted[r.channel],
            )
            for r in readiness
        ],
    )


def _build_channel_row(
    channel: str,
    readiness: list[NeurocommentReadiness],
    linked: LinkedDiscussionGroup | None,
    flags: _ChannelFlags,
) -> NeurocommentChannelRow:
    rows = [r for r in readiness if r.channel == channel]
    return NeurocommentChannelRow(
        channel=channel,
        status=_channel_status(rows, linked, challenged=flags.challenged, paused=flags.paused),
        ready_accounts=sum(1 for r in rows if r.ready),
        total_accounts=len(rows),
        deleted_recent=flags.deleted_recent,
    )


# The pair verdicts a CHANNEL row has no word of its own for. ``rejoin_exhausted`` is
# per-account by construction (``ChannelStatus``: the channel keeps whatever its other
# accounts make of it), and ``not_ready`` is the pair-level catch-all this row has always
# badged ``throttled``.
_AS_CHANNEL: dict[str, ChannelStatus] = {
    "rejoin_exhausted": "join_failed",
    "not_ready": "throttled",
}
# Applied to the SET of per-row verdicts rather than walked row by row, which gives the same
# answer: every rung of ``_pair_status.pair_block_reason`` already excludes the rungs above
# it, so per-row precedence and channel-wide precedence coincide.
_CHANNEL_PRIORITY: tuple[ChannelStatus, ...] = (
    "banned",
    "chat_restricted",
    "rejoining",
    "join_failed",
    "join_by_request",
    "throttled",
)


def _channel_status(
    rows: list[NeurocommentReadiness],
    linked: LinkedDiscussionGroup | None,
    *,
    challenged: bool,
    paused: bool,
) -> ChannelStatus:
    """Aggregate a channel's status from its readiness rows + linked-group cache.

    Precedence: a comments-off channel can never be commented on; a channel serving out
    a "will not let us write" round (Ф2 #147) is paused regardless of readiness; otherwise
    an account that's ready wins — read off ``rows`` here rather than taken as a count from
    the caller, because a count that disagreed with the rows beside it (all it took was
    passing 0 for rows that do carry a ready one) silently walked the ladder below.

    Below that the row-derived verdicts are ``_pair_status.pair_block_reason``'s — the SAME
    ladder ``engine`` reports its selection misses through, so badge and activity log read a
    pair's own row the same way, rung for rung. One deliberate divergence: the operator skip
    (#148) is checked in ``engine`` ABOVE that ladder and is not a channel-level state at
    all, so a pair the operator took out of service logs ``human_skipped`` while its channel
    badges whatever else its row says — a skipped pair that also failed its bot check reads
    ``chat_restricted`` here.

    This function keeps only what a readiness row cannot answer: the two channel-level gates
    above, the empty-rows reading, and ``bot_challenge``, which is ``chat_restricted`` plus a
    guardian-bot challenge row for the channel (#145).
    """
    if linked is not None and not linked.comments_enabled:
        return "comments_off"
    if paused:
        return "channel_paused"
    if any(r.ready for r in rows):
        return "ready"
    if not rows:
        return "no_data"  # onboarding hasn't produced readiness data for this channel yet
    blocked = {
        _AS_CHANNEL.get(reason, reason)
        for r in rows
        if (reason := _pair_status.pair_block_reason(r)) is not None
    }
    status = next((s for s in _CHANNEL_PRIORITY if s in blocked), "throttled")
    return "bot_challenge" if challenged and status == "chat_restricted" else status
