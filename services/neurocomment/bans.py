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
        # A restricted verdict is the one authoritative per-group ban signal, so run the
        # confirmation ladder on it (state passed in — the probe above already paid for it)
        # and leave the group if it holds. There is no mirror branch: a can_send verdict
        # does NOT lift a ban. The pair-level ban is permanent by decision (#30), so this
        # button REPORTS the channel's state and only ever adds bans — never removes one.
        for account_id, state in zip(serving, states, strict=True):
            if state == "restricted":
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
        # INFO, not WARNING: this is the NEGATIVE answer to a question we asked, and it is
        # the common one — every gated post runs the ladder, so a closed channel produced
        # two amber rows per blocked post and the operator's warning filter filled up with
        # a check that found nothing wrong. The row that IS the problem (the gate, the
        # refused write) is logged by the caller at WARNING and still says so.
        await log_event(
            "INFO",
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
    # Sticky (#30), and deliberately so: for this pair the channel is closed for good.
    # There is no way back, and nothing in the codebase offers one. "Проверить каналы"
    # used to un-ban on a live can_send verdict, defended as unreachable because a pair we
    # left can only probe as not_member — but the leave below is best-effort and has its
    # own failing-leave test, so that verdict WAS reachable and the button quietly undid
    # the ban the hints call permanent. It was removed rather than the hints softened.
    # The operator has no path either: ``challenge.retry_pair`` would delete the readiness
    # row and re-onboard, and ``POST /api/v1/neurocomment/retry`` still reaches it, but its
    # only caller is the captcha-queue button, which is fed by ``list_campaign_challenges``
    # — and a banned pair has no challenge row, so no button in the UI points here. The
    # remedy is another account in the campaign; when every serving account is banned the
    # channel is unlinked below. Marked BEFORE the leave — the ban is the truth and must
    # persist even if the leave RPC fails.
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
    per-account channel subset, and any serving account with NO row keeps the channel: a
    missing row means that account was never tried here, not that it failed. Onboarding
    has no timer, so the fleet reaches a freshly linked channel slowly; counting only the
    rows that exist would let the first banned account drop a channel the other five
    never touched.

    Every row present must then be in a TERMINAL state — banned (#30) or operator-skipped
    (#148). Both are permanent verdicts on the pair, and reading a skip as "still usable"
    meant five bans plus one skip held a channel that produces nothing, forever: a per-pair
    ban has no un-ban path, so nothing would ever revisit it. The three sibling rules
    phrase the same clause as "no serving row is ``ready``", which would also fix this —
    but this rule is the one with no clock of its own. ``_sweep`` and ``_rejoin`` overrule
    the other accounts only after their own 48h / four days have run out, whereas one ban
    lands here mid-post; a pair still inside its approval window is not ready either, and
    dropping on that would cancel patience those two rules are still counting out.
    """
    campaign = await fetch_active_campaign_for_channel(channel)
    if campaign is None:
        return
    links = (await list_campaign_accounts(campaign.campaign_id)).links
    serving = serving_accounts(links, channel)
    rows = (await list_channel_readiness(campaign.campaign_id, channel, serving)).readiness
    if len(rows) != len(serving) or any(not (row.banned or row.human_skipped) for row in rows):
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
            # The rows, not their count: a skipped pair rides along in the drop but was
            # never banned, and reporting it as one sends the operator hunting a ban that
            # does not exist.
            "banned_accounts": sum(1 for row in rows if row.banned),
            "reason": "all_accounts_banned",
        },
    )
