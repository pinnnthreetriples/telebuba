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
)
from schemas.neurocomment import (
    AccountChannelReadiness,
    ChannelStatus,
    NeurocommentAccountCard,
    NeurocommentBoard,
    NeurocommentChannelRow,
)
from services.neurocomment import _rejoin, _state, settings_store

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

    now = datetime.now(UTC)
    day_ago = (now - timedelta(days=1)).isoformat()
    posted = (await list_posted_comments_since(campaign_id, day_ago)).comments

    cards = [
        _build_card(
            signals=_AccountSignals(
                account=accounts[account_id],
                pinned_channels=pins.get(account_id, []),
            ),
            readiness=[r for r in readiness if r.account_id == account_id],
            posted=[c for c in posted if c.account_id == account_id],
            max_comments_per_hour=limits.max_comments_per_hour,
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
    deleted_by_channel: dict[str, int] = {}
    for comment in delivered:
        if comment.deleted_at:
            deleted_by_channel[comment.channel] = deleted_by_channel.get(comment.channel, 0) + 1
    rows = [
        _build_channel_row(
            link.channel,
            readiness,
            linked.get(link.channel),
            _ChannelFlags(
                challenged=link.channel in challenged,
                paused=_state.channel_paused(link.paused_until, now),
                deleted_recent=deleted_by_channel.get(link.channel, 0),
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
    account, pinned_channels = signals
    hour_ago = (now - timedelta(hours=1)).isoformat()
    last_hour = sum(1 for c in posted if c.created_at >= hour_ago)
    latest = max(posted, key=lambda c: c.created_at, default=None)
    return NeurocommentAccountCard(
        account_id=account.account_id,
        label=account.label or account.account_id,
        comments_last_hour=last_hour,
        max_comments_per_hour=max_comments_per_hour,
        comments_today=len(posted),
        deleted_today=sum(1 for c in posted if c.deleted_at),
        last_comment_at=latest.created_at if latest else None,
        last_comment_text=latest.comment_text if latest else None,
        pinned_channels=pinned_channels,
        readiness=[
            AccountChannelReadiness(
                channel=r.channel,
                ready=r.ready,
                joined=r.joined,
                captcha_passed=r.captcha_passed,
                human_skipped=r.human_skipped,
                # Both of the "this pair is out of service" flags travel to the card:
                # the channel row aggregates them away (a channel with one ready
                # account reads ``ready``), so the card is the only surface that can
                # say which account is banned (#30) or skipped (#148) where.
                banned=r.banned,
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
    ready_count = sum(1 for r in rows if r.ready)
    return NeurocommentChannelRow(
        channel=channel,
        status=_channel_status(
            rows, linked, ready_count, challenged=flags.challenged, paused=flags.paused
        ),
        ready_accounts=ready_count,
        total_accounts=len(rows),
        deleted_recent=flags.deleted_recent,
    )


def _channel_status(
    rows: list[NeurocommentReadiness],
    linked: LinkedDiscussionGroup | None,
    ready_count: int,
    *,
    challenged: bool,
    paused: bool,
) -> ChannelStatus:
    """Aggregate a channel's status from its readiness rows + linked-group cache.

    Precedence: a comments-off channel can never be commented on; a channel serving out
    a "will not let us write" round (Ф2 #147) is paused regardless of
    readiness; otherwise an account that's ready wins; then an auto-ban (#30) when no
    account is ready but one is banned here; then the joined-but-blocked failure modes
    — ``bot_challenge`` when a guardian-bot challenge row exists for the channel
    (#145), else ``chat_restricted`` (a Telegram-level write block) — then the
    not-joined readings, which :func:`_not_joined_status` splits.
    """
    if linked is not None and not linked.comments_enabled:
        return "comments_off"
    if paused:
        return "channel_paused"
    if ready_count > 0:
        return "ready"
    if any(r.banned for r in rows):
        return "banned"
    if any(r.joined and not r.captcha_passed for r in rows):
        return "bot_challenge" if challenged else "chat_restricted"
    return _not_joined_status(rows)


def _not_joined_status(rows: list[NeurocommentReadiness]) -> ChannelStatus:
    """Status for a channel none of whose accounts are joined-and-ready.

    The unjoined-but-captcha_passed row is ONE sentinel with two readings, and the badge
    used to give both the terminal one. It is written by a hard join failure AND by an
    account kicked out of the chat (``_rejoin``'s module docstring: both mean "this pair
    needs a fresh join"), and since the re-join rule shipped it does self-resolve — one
    attempt within minutes, then one a day, four in all. So a pair with attempts left is
    ``rejoining`` (the re-join timeline is running), and only a pair that has spent them
    — or one ``_rejoin`` refuses to retry at all, i.e. skipped (#148) — is the terminal
    ``join_failed``. Since #44 the row also says WHY the pair is out, and ``exhausted``
    folds that in: a verdict a re-join can never beat (a handle nobody owns, a revoked
    invite key) is finished on arrival, so the badge stops offering «Возвращаемся в чат»
    for a chat nothing is walking back into. Then the approval gate ``join_by_request``;
    ``throttled`` is the catch-all.
    """
    if not rows:
        return "no_data"  # onboarding hasn't produced readiness data for this channel yet
    # Both halves read straight off the rule that does the retrying, never a second copy
    # of its threshold: ``access_lost`` is the retryable half of the sentinel (it excludes
    # the skipped and banned rows the rule will not touch) and ``exhausted`` is the budget
    # it spends. A banned row never reaches here — ``banned`` wins one branch up.
    if any(_rejoin.access_lost(r) and not _rejoin.exhausted(r) for r in rows):
        return "rejoining"
    if any(not r.joined and r.captcha_passed for r in rows):
        return "join_failed"
    if any(not r.joined for r in rows):
        return "join_by_request"
    return "throttled"
