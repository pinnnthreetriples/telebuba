"""Neurocomment work-view read model (issue #119).

Builds the board the UI polls for one campaign: one card per serving account
(quota usage, health, last comment) plus one row per watched channel (aggregate
status). Every DB row is bulk-loaded once here — no per-card N+1, mirroring
``services.warming.board.load_board``.

Account health reuses the warming readiness gate + Trust Score from already-
loaded signals (``account_trust_score_from``); the only neurocomment-specific
logic is the per-channel status derivation.
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
    list_device_fingerprints_by_ids,
    list_linked_groups,
    list_posted_comments_since,
    list_spam_statuses_by_ids,
    list_warming_states_by_ids,
)
from schemas.neurocomment import (
    AccountChannelReadiness,
    ChannelStatus,
    NeurocommentAccountCard,
    NeurocommentBoard,
    NeurocommentChannelRow,
)
from services.neurocomment import _state
from services.trust import account_trust_score_from
from services.warming.pacing import evaluate_readiness

if TYPE_CHECKING:
    from schemas.accounts import AccountRead
    from schemas.device_fingerprint import DeviceFingerprint
    from schemas.neurocomment import (
        CommentRecord,
        LinkedDiscussionGroup,
        NeurocommentReadiness,
    )
    from schemas.spam_status import SpamStatusVerdict
    from schemas.warming import WarmingStateRecord


class _AccountSignals(NamedTuple):
    """The bulk-loaded per-account signals that travel together into a card."""

    account: AccountRead
    record: WarmingStateRecord | None
    spam: SpamStatusVerdict | None
    fingerprint: DeviceFingerprint | None
    pinned_channel: str | None  # channel pin, or None when the account serves all


async def load_neurocomment_board(campaign_id: str) -> NeurocommentBoard | None:
    """Assemble the work-view board for one campaign, or ``None`` if it is gone."""
    campaign = await fetch_campaign(campaign_id)
    if campaign is None:
        return None

    account_links = (await list_campaign_accounts(campaign_id)).links
    account_ids = [link.account_id for link in account_links]
    pins = {link.account_id: link.channel for link in account_links}
    channels = [link.channel for link in (await list_campaign_channels(campaign_id)).links]

    accounts = {acc.account_id: acc for acc in (await list_accounts_by_ids(account_ids)).accounts}
    readiness = (await list_campaign_readiness(campaign_id)).readiness
    linked = {g.channel: g for g in (await list_linked_groups(channels)).groups}
    challenged = set((await list_challenged_channels(channels)).channels)

    now = datetime.now(UTC)
    day_ago = (now - timedelta(days=1)).isoformat()
    posted = (await list_posted_comments_since(campaign_id, day_ago)).comments

    records = {rec.account_id: rec for rec in await list_warming_states_by_ids(account_ids)}
    spam_by_account = await list_spam_statuses_by_ids(account_ids)
    fingerprints = await list_device_fingerprints_by_ids(account_ids)
    channel_count = max(1, len(channels))

    cards = [
        _build_card(
            signals=_AccountSignals(
                account=accounts[account_id],
                record=records.get(account_id),
                spam=spam_by_account.get(account_id),
                fingerprint=fingerprints.get(account_id),
                pinned_channel=pins.get(account_id),
            ),
            readiness=[r for r in readiness if r.account_id == account_id],
            posted=[c for c in posted if c.account_id == account_id],
            channel_count=channel_count,
            now=now,
        )
        for account_id in account_ids
        if account_id in accounts
    ]
    rows = [
        _build_channel_row(
            channel,
            readiness,
            linked.get(channel),
            challenged=channel in challenged,
            backed_off=_state.is_channel_in_challenge_backoff(channel, now),
        )
        for channel in channels
    ]
    return NeurocommentBoard(
        campaign_id=campaign.campaign_id,
        campaign_name=campaign.name,
        status=campaign.status,
        solver_enabled=campaign.solver_enabled,
        accounts=cards,
        channels=rows,
    )


def _build_card(
    *,
    signals: _AccountSignals,
    readiness: list[NeurocommentReadiness],
    posted: list[CommentRecord],
    channel_count: int,
    now: datetime,
) -> NeurocommentAccountCard:
    nc = settings.neurocomment
    account, record, spam, fingerprint, pinned_channel = signals
    trust = account_trust_score_from(
        account=account,
        record=record,
        spam=spam,
        lang_code=fingerprint.system_lang_code if fingerprint else None,
        now=now,
    )
    health = evaluate_readiness(account, channel_count, spam=spam, trust_score=trust)
    hour_ago = (now - timedelta(hours=1)).isoformat()
    last_hour = sum(1 for c in posted if c.created_at >= hour_ago)
    latest = max(posted, key=lambda c: c.created_at, default=None)
    return NeurocommentAccountCard(
        account_id=account.account_id,
        label=account.label or account.account_id,
        health="ready" if health.ready else "blocked",
        trust_score=trust.score,
        trust_band=trust.band,
        spam_status=spam.status if spam else None,
        comments_last_hour=last_hour,
        max_comments_per_hour=nc.max_comments_per_hour,
        comments_today=len(posted),
        last_comment_at=latest.created_at if latest else None,
        last_comment_text=latest.comment_text if latest else None,
        pinned_channel=pinned_channel,
        readiness=[
            AccountChannelReadiness(
                channel=r.channel,
                ready=r.ready,
                joined=r.joined,
                captcha_passed=r.captcha_passed,
            )
            for r in readiness
        ],
    )


def _build_channel_row(
    channel: str,
    readiness: list[NeurocommentReadiness],
    linked: LinkedDiscussionGroup | None,
    *,
    challenged: bool,
    backed_off: bool,
) -> NeurocommentChannelRow:
    rows = [r for r in readiness if r.channel == channel]
    ready_count = sum(1 for r in rows if r.ready)
    return NeurocommentChannelRow(
        channel=channel,
        status=_channel_status(
            rows, linked, ready_count, challenged=challenged, backed_off=backed_off
        ),
        ready_accounts=ready_count,
        total_accounts=len(rows),
    )


def _channel_status(
    rows: list[NeurocommentReadiness],
    linked: LinkedDiscussionGroup | None,
    ready_count: int,
    *,
    challenged: bool,
    backed_off: bool,
) -> ChannelStatus:
    """Aggregate a channel's status from its readiness rows + linked-group cache.

    Precedence: a comments-off channel can never be commented on; a channel in
    challenge back-off (Ф2 #147, K solver failures) is paused regardless of
    readiness; otherwise an account that's ready wins; then the joined-but-blocked
    failure modes — ``bot_challenge`` when a guardian-bot challenge row exists for
    the channel (#145), else ``chat_restricted`` (a Telegram-level write block) —
    then, for a not-joined row, ``join_failed`` (onboarding's hard-failure sentinel)
    vs ``join_by_request`` (approval gate); ``throttled`` is the catch-all.
    """
    if linked is not None and not linked.comments_enabled:
        return "comments_off"
    if backed_off:
        return "bot_challenge_backoff"
    if ready_count > 0:
        return "ready"
    if any(r.joined and not r.captcha_passed for r in rows):
        return "bot_challenge" if challenged else "chat_restricted"
    return _not_joined_status(rows)


def _not_joined_status(rows: list[NeurocommentReadiness]) -> ChannelStatus:
    """Status for a channel none of whose accounts are joined-and-ready.

    ``join_failed`` is onboarding's hard-failure sentinel (joined=False,
    captcha_passed=True) — a terminal join failure that never self-resolves, distinct
    from the approval gate ``join_by_request``; ``throttled`` is the catch-all.
    """
    if not rows:
        return "no_data"  # onboarding hasn't produced readiness data for this channel yet
    if any(not r.joined and r.captcha_passed for r in rows):
        return "join_failed"
    if any(not r.joined for r in rows):
        return "join_by_request"
    return "throttled"
