"""Live per-channel ban check for a campaign's channels ("Проверить каналы").

For each channel, probe every serving account's participant state in the linked
discussion group (read-only ``CheckBannedInChannel`` — no message sent) and
aggregate a per-channel verdict. Pin-aware account resolution mirrors
``engine._select_account``; probes are bounded by a semaphore so a burst of
``GetParticipant`` reads can't trip flood limits. A probe fault degrades to
``unknown`` — the check never crashes.

Also home to :func:`confirm_group_ban_and_leave`, the single gate in front of the
sticky auto-ban (#30) and the group leave — see its docstring for why
``UserBannedInChannelError`` is not itself evidence of a per-group ban.
"""

from __future__ import annotations

import asyncio
from typing import Literal

from core.config import settings
from core.db import (
    clear_pair_banned,
    fetch_active_campaign_for_channel,
    fetch_campaign,
    list_campaign_accounts,
    list_campaign_channels,
    list_channel_readiness,
    mark_pair_banned,
    upsert_readiness,
)
from core.logging import log_event
from schemas.neurocomment_bans import ChannelBanCheck, ChannelBanCheckList
from schemas.telegram_actions import BanCheckResult, CheckBannedInChannel, LeaveDiscussionGroup
from services.neurocomment import _seams
from services.neurocomment._pins import serving_accounts

_ChannelStatus = Literal["ok", "banned", "unknown"]


def _aggregate(states: list[str]) -> _ChannelStatus:
    """A channel is ok if any account can comment, banned if all are blocked."""
    if any(state == "can_send" for state in states):
        return "ok"
    if any(state in ("restricted", "not_member") for state in states):
        return "banned"
    return "unknown"


async def check_campaign_channel_bans(campaign_id: str) -> ChannelBanCheckList | None:
    """Probe each campaign channel for bans, or ``None`` if the campaign is gone."""
    campaign = await fetch_campaign(campaign_id)
    if campaign is None:
        return None

    account_links = (await list_campaign_accounts(campaign_id)).links
    channels = [link.channel for link in (await list_campaign_channels(campaign_id)).links]
    semaphore = asyncio.Semaphore(settings.neurocomment.ban_check_concurrency)

    async def _probe(account_id: str, channel: str) -> str:
        async with semaphore:
            try:
                result = await _seams.execute_read(
                    account_id, CheckBannedInChannel(channel=channel)
                )
            except Exception:  # noqa: BLE001 - a probe fault degrades to "unknown".
                return "unknown"
        return result.state if isinstance(result, BanCheckResult) else "unknown"

    async def _check_channel(channel: str) -> ChannelBanCheck:
        # Pin rule: unpinned accounts serve every channel; pinned only their own.
        serving = serving_accounts(account_links, channel)
        if not serving:
            return ChannelBanCheck(channel=channel, status="unknown")
        states = await asyncio.gather(*(_probe(acc, channel) for acc in serving))
        # Recovery (#30): a live can_send verdict is proof the account may write again,
        # so lift any sticky auto-ban on that pair — this button is the un-ban path.
        # A restricted verdict is the mirror image: it is the one authoritative
        # per-group ban signal, so run the confirmation ladder on it (state passed in —
        # the probe above already paid for it) and leave the group if it holds.
        for account_id, state in zip(serving, states, strict=True):
            if state == "can_send":
                await clear_pair_banned(account_id, channel)
            elif state == "restricted":
                await confirm_group_ban_and_leave(account_id, channel, known_state=state)
        return ChannelBanCheck(channel=channel, status=_aggregate(list(states)))

    items = await asyncio.gather(*(_check_channel(channel) for channel in channels))
    return ChannelBanCheckList(items=list(items))


async def confirm_group_ban_and_leave(
    account_id: str,
    channel: str,
    *,
    known_state: str | None = None,
) -> bool:
    """Confirm ``account_id`` is banned in THIS group; if so mark the pair and leave.

    ``UserBannedInChannelError`` is Telegram's ACCOUNT-WIDE anti-spam write
    restriction — the same state @SpamBot reports as limited — not a moderator
    action in one group. Per-chat signals are different errors entirely: an admin
    mute surfaces as ``ChatWriteForbiddenError``, a kick as
    ``UserNotParticipantError`` / ``ChannelPrivateError``, and both are routed to
    their own branches. So that error alone must never park a pair: a globally
    limited account would otherwise collect sticky bans on whatever channels it
    happened to post to, one at a time.

    The only authoritative per-group evidence is the group's OWN participant
    record: ``CheckBannedInChannel`` → ``restricted``
    (``ChannelParticipantBanned`` with ``send_messages`` revoked), which an
    account-wide restriction cannot produce. ``not_member`` is explicitly NOT
    proof — it collapses kicked / never-joined / left, and there is nothing to
    leave anyway. Confirmation is that verdict plus a ``clean`` @SpamBot reading:
    with the account ``limited`` the write block is global, so the group is not at
    fault, and an ``unknown`` reading means the probe never reached @SpamBot, so
    nothing is proven either way.

    ``known_state`` feeds in an already-known probe result so the "Проверить
    каналы" button does not pay a second ``GetParticipant`` per pair.

    Returns True only when the pair was marked banned; a failed leave never raises.
    """
    state = known_state
    if state is None:
        try:
            probe = await _seams.execute_read(account_id, CheckBannedInChannel(channel=channel))
        except Exception:  # noqa: BLE001 - a probe fault is not evidence of a ban.
            state = "probe_error"
        else:
            state = probe.state if isinstance(probe, BanCheckResult) else "probe_error"
    if state != "restricted":
        await log_event(
            "WARNING",
            "neurocomment_group_ban_unconfirmed",
            account_id=account_id,
            extra={"channel": channel, "state": state},
        )
        return False
    verdict = await _seams.refresh_spam_status(account_id, force=True)
    if verdict.status != "clean":
        await log_event(
            "WARNING",
            "neurocomment_group_ban_account_limited",
            account_id=account_id,
            extra={"channel": channel, "state": state, "spam_status": verdict.status},
        )
        return False
    # The row must exist first — ``mark_pair_banned`` is a plain UPDATE, not an upsert.
    # The field values mirror ``_classify``'s ban branch: we are a participant of the
    # group (that is what the restricted record means) but cannot write there.
    await upsert_readiness(account_id, channel, joined=True, captcha_passed=False, ready=False)
    # Sticky (#30), and deliberately so. The way back is the operator's per-pair retry
    # (``services.neurocomment.challenge.retry_pair``), which deletes the readiness row
    # and re-onboards — NOT the can_send probe that clears an ordinary ban above: once
    # we leave, this pair can only ever probe as not_member. Marked BEFORE the leave —
    # the ban is the truth and must persist even if the leave RPC fails.
    await mark_pair_banned(account_id, channel)
    try:
        leave = await _seams.execute(account_id, LeaveDiscussionGroup(channel=channel))
        outcome = leave.status
    except Exception as exc:  # noqa: BLE001 - the mark stands; the leave is best-effort.
        outcome = type(exc).__name__
    await log_event(
        "WARNING",
        "neurocomment_group_ban_confirmed",
        account_id=account_id,
        extra={"channel": channel, "leave": outcome},
    )
    await _unlink_channel_if_no_account_left(account_id, channel)
    return True


async def _unlink_channel_if_no_account_left(account_id: str, channel: str) -> None:
    """Drop ``channel`` from its campaign once every serving account is banned there.

    A channel nobody can write in produces nothing but failed posts, so it is
    unlinked through the service (not the repository) exactly like the join-request
    expiry in ``_sweep._drop_unapproved_channel`` — that is what makes the running
    listener reconcile and stop watching it.

    The verdict is read from persisted readiness, never a live probe: this runs on the
    post hot path and must not spend Telegram calls. Serving accounts respect the
    per-account channel subset, and one banned account must never remove a channel the
    others comment in fine — so any serving account whose row is NOT banned keeps the
    channel, and so does any serving account with NO row: a missing row means that
    account was never tried here, not that it failed. Onboarding has no timer, so the
    fleet reaches a freshly linked channel slowly; counting only the rows that exist
    would let the first banned account drop a channel the other five never touched.
    """
    campaign = await fetch_active_campaign_for_channel(channel)
    if campaign is None:
        return
    links = (await list_campaign_accounts(campaign.campaign_id)).links
    serving = serving_accounts(links, channel)
    rows = (await list_channel_readiness(campaign.campaign_id, channel, serving)).readiness
    if len(rows) != len(serving) or any(not row.banned for row in rows):
        return
    # Late import: ``campaigns`` reaches ``_runtime``, which reaches this module — the
    # same cycle ``_sweep._drop_unapproved_channel`` dodges the same way.
    from services.neurocomment import campaigns as campaigns_service  # noqa: PLC0415

    await campaigns_service.deactivate_channel(campaign.campaign_id, channel)
    await log_event(
        "WARNING",
        "neurocomment_channel_all_accounts_banned",
        account_id=account_id,
        extra={
            "channel": channel,
            "campaign_id": campaign.campaign_id,
            "banned_accounts": len(rows),
            "reason": "all_accounts_banned",
        },
    )
