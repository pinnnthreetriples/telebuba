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
    list_accounts,
    list_campaign_accounts,
    list_campaign_channels,
    list_campaign_readiness,
    list_challenged_channels,
    list_device_fingerprints,
    list_linked_groups,
    list_posted_comments_since,
    list_spam_statuses,
    list_warming_states,
)
from schemas.neurocomment import (
    AccountChannelReadiness,
    ChannelStatus,
    NeurocommentAccountCard,
    NeurocommentBoard,
    NeurocommentChannelRow,
)
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


async def load_neurocomment_board(campaign_id: str) -> NeurocommentBoard | None:
    """Assemble the work-view board for one campaign, or ``None`` if it is gone."""
    campaign = await fetch_campaign(campaign_id)
    if campaign is None:
        return None

    account_ids = [link.account_id for link in (await list_campaign_accounts(campaign_id)).links]
    channels = [link.channel for link in (await list_campaign_channels(campaign_id)).links]

    accounts = {acc.account_id: acc for acc in (await list_accounts()).accounts}
    readiness = (await list_campaign_readiness(campaign_id)).readiness
    linked = {g.channel: g for g in (await list_linked_groups(channels)).groups}
    challenged = set((await list_challenged_channels(channels)).channels)

    now = datetime.now(UTC)
    day_ago = (now - timedelta(days=1)).isoformat()
    posted = (await list_posted_comments_since(campaign_id, day_ago)).comments

    records = {rec.account_id: rec for rec in await list_warming_states()}
    spam_by_account = await list_spam_statuses()
    fingerprints = await list_device_fingerprints()
    channel_count = max(1, len(channels))

    cards = [
        _build_card(
            signals=_AccountSignals(
                account=accounts[account_id],
                record=records.get(account_id),
                spam=spam_by_account.get(account_id),
                fingerprint=fingerprints.get(account_id),
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
        )
        for channel in channels
    ]
    return NeurocommentBoard(
        campaign_id=campaign.campaign_id,
        campaign_name=campaign.name,
        status=campaign.status,
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
    account, record, spam, fingerprint = signals
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
    last_comment_at = max((c.created_at for c in posted), default=None)
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
        last_comment_at=last_comment_at,
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
) -> NeurocommentChannelRow:
    rows = [r for r in readiness if r.channel == channel]
    ready_count = sum(1 for r in rows if r.ready)
    return NeurocommentChannelRow(
        channel=channel,
        status=_channel_status(rows, linked, ready_count, challenged=challenged),
        ready_accounts=ready_count,
        total_accounts=len(rows),
    )


def _channel_status(
    rows: list[NeurocommentReadiness],
    linked: LinkedDiscussionGroup | None,
    ready_count: int,
    *,
    challenged: bool,
) -> ChannelStatus:
    """Aggregate a channel's status from its readiness rows + linked-group cache.

    Precedence: a comments-off channel can never be commented on; otherwise an
    account that's ready wins; then the joined-but-blocked failure modes —
    ``bot_challenge`` when a guardian-bot challenge row exists for the channel
    (Ф2 #145), else ``chat_restricted`` (a Telegram-level write block) — then the
    approval gate when not joined; ``throttled`` is the catch-all.

    ``bot_challenge_backoff`` is in the enum but not derived here yet (the channel
    back-off counter lands in a later slice).
    """
    if linked is not None and not linked.comments_enabled:
        return "comments_off"
    if ready_count > 0:
        return "ready"
    if any(r.joined and not r.captcha_passed for r in rows):
        return "bot_challenge" if challenged else "chat_restricted"
    if any(not r.joined for r in rows):
        return "join_by_request"
    return "throttled"
