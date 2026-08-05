"""Live per-channel ban check for a campaign's channels ("Проверить каналы").

For each channel, probe every serving account's participant state in the linked
discussion group (read-only ``CheckBannedInChannel`` — no message sent) and
aggregate a per-channel verdict. Pin-aware account resolution mirrors
``engine._select_account``; probes are bounded by a semaphore so a burst of
``GetParticipant`` reads can't trip flood limits. A probe fault degrades to
``unknown`` — the check never crashes.

Also home to the two verdicts that can park a pair for good:
:func:`confirm_group_ban_and_leave`, the gate in front of the sticky auto-ban (#30) and
the group leave — see its docstring for why ``UserBannedInChannelError`` is not itself
evidence of a per-group ban — and :func:`register_unconfirmed_ban` (#47), which bounds
how many times a group may refuse a HEALTHY account before the pair gives the chat up
anyway. Both read the same probe (:func:`probe_group_state`, taken once per refusal) and
both end in the same exit, ``_mark_banned_and_leave``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Literal

from core.config import settings
from core.db import (
    fetch_active_campaign_for_channel,
    fetch_campaign,
    list_campaign_accounts,
    list_campaign_channels,
    list_channel_readiness,
    mark_pair_banned,
    stamp_unconfirmed_ban,
    unconfirmed_ban_is_countable,
    upsert_readiness,
)
from core.logging import log_event
from schemas.neurocomment_bans import ChannelBanCheck, ChannelBanCheckList
from schemas.telegram_actions import BanCheckResult, CheckBannedInChannel, LeaveDiscussionGroup
from services.neurocomment import _seams, _state
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


async def probe_group_state(account_id: str, channel: str) -> str:
    """Read the group's OWN participant record for this pair; ``probe_error`` on a fault.

    One refusal, one ``GetParticipant``. Both verdicts below ask the same question of the
    same record — did THIS group ban us — so the post path takes the answer once here and
    hands it to each as ``known_state`` instead of probing twice for one failed send.
    """
    try:
        probe = await _seams.execute_read(account_id, CheckBannedInChannel(channel=channel))
    except Exception:  # noqa: BLE001 - a probe fault is not evidence of a ban.
        return "probe_error"
    return probe.state if isinstance(probe, BanCheckResult) else "probe_error"


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

    ONE negative answer is not the end of the story on the post path: the ``can_send``
    one — the group's own record says this account may write and @SpamBot says it is
    clean, yet the send was refused anyway — is counted by
    :func:`register_unconfirmed_ban`, and the SECOND such refusal, at least a day after
    the first and inside the same 48h, takes the pair out of the chat on the budget
    instead of on proof. The other negatives are counted by nobody, for the reason this
    function refuses to act on them.

    Returns True only when the pair was marked banned; a failed leave never raises.
    """
    state = known_state if known_state is not None else await probe_group_state(account_id, channel)
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
    await _mark_banned_and_leave(account_id, channel, "neurocomment_group_ban_confirmed")
    return True


async def register_unconfirmed_ban(
    account_id: str,
    channel: str,
    *,
    known_state: str | None = None,
) -> str | None:
    """Count a refusal by a group that has no excuse for it; park the pair on the second.

    The verdict lands ON that second counted refusal — a day after the first, not at the
    end of the 48h window the three sibling rules sit out in full. Deliberate: by then the
    pair has been refused twice, a day apart, by a group whose own record says it may
    write, and waiting out the rest of the window only buys more of the same.

    The gap :func:`confirm_group_ban_and_leave` deliberately leaves open, closed with a
    budget instead of a weaker proof. That function is right that one
    ``UserBannedInChannelError`` proves nothing about THIS group — but a pair the group
    refuses over and over, while the group's own participant record says ``can_send`` and
    @SpamBot calls the account clean, is not producing comments here either way. Live DB:
    one account met the same channel four times running and the pair was never marked, so
    selection kept handing it the same post — three days, ten refusals, zero comments.

    That ``can_send`` + ``clean`` reading is the ONLY one counted. Every other answer
    points away from this group and must not spend the budget: ``restricted`` with a
    limited account, or any reading at all with a limited account, is the account-wide
    block — and that error then lands in EVERY channel it posts to, so counting it would
    walk one limited account into a sticky ban on the whole fleet's channels, one at a
    time, with no way back for either the code or the operator. ``probe_error`` proves
    nothing whatsoever, and ``not_member`` has no chat left to leave.

    @SpamBot is asked only once every cheap refusal has had its chance, and that order is
    load-bearing: the reading is served from cache only inside ``spam_status_ttl_hours``, so
    past that it opens a REAL dialogue with @SpamBot — on the post hot path, per refusal,
    and a failed probe is not cached at all, so a struggling account would repeat it every
    time. Anything this rule can decide on its own (the wrong participant state, an interval
    that has not run out) therefore decides before that call is made.

    The budget is the one the sibling rules already spend, read off ``channel_max_rounds``
    and ``channel_pause_hours`` rather than added as knobs of its own — but never fewer
    than two rounds, because a single refusal must not be enough to close a channel for
    good; an operator who tunes the setting down to 1 is shortening the sibling rules'
    patience, not handing this one a sticky ban on the first failure.

    Two counted refusals then need two things: they must be at least
    ``channel_pause_hours`` apart, and both must fall inside
    ``channel_pause_hours * rounds`` — a day and 48h at the shipped settings. The interval
    is what makes "two in 48h" true rather than "two in an hour", and BOTH conditions are
    resolved inside ``stamp_unconfirmed_ban``'s single UPDATE. That is the whole reason
    they are there and not here: as a Python check the interval sat an ``await`` away from
    the write, so two refusals whose coroutines interleaved both passed it and both counted
    — and a channel with a queue of posts took a pair from its first refusal to a permanent
    ban in seconds, running the leave once per count. A refusal inside the interval now
    writes nothing at all, so it does not move the stamp either. Above the window the count
    starts over, and a delivered comment clears it outright (``clear_unconfirmed_bans``) —
    both say the same thing, that this pair is not stuck.

    Only the unconfirmed ban error reaches here. An admin mute (``ChatWriteForbiddenError``)
    and a kick (``ChannelPrivateError`` / ``UserNotParticipantError``) mean something else
    and have their own branches; counting them here would turn a channel-wide gate into a
    per-pair ban.

    On the last refusal the pair takes exactly the confirmed ban's exit — marked, out of
    the chat, and the channel unlinked only once every serving account has gone the same
    way. That exit logs the ban itself, so the caller reports the POST outcome and no
    second copy of the same line.

    Returns the refusal's position in the budget (``"1/2"``, then ``"2/2"`` — the one that
    parked the pair) so the caller's own line can carry it, or ``None`` when the refusal
    was NOT counted. That distinction is the whole point of reporting a position at all:
    every early return below is a refusal this rule charges nobody for, and a counter
    printed next to one would tell the operator a budget is running down when it is not.
    """
    if known_state != "can_send":
        return None
    nc = settings.neurocomment
    now = datetime.now(UTC)
    pause = timedelta(hours=nc.channel_pause_hours)
    # The pause a counted refusal parks this pair with IS the minimum interval, so a
    # refusal arriving while it stands is the same episode, not a second attempt. The
    # pair's own stamp is what that is read off — not the in-memory cooldown, which a
    # concurrent refusal reaches before the first one has set it.
    interval_start = (now - pause).isoformat()
    # Cheap, and NOT the guard (the stamp's own UPDATE is — see its docstring): it is here
    # so a refusal the interval already rules out never reaches @SpamBot below.
    if not await unconfirmed_ban_is_countable(account_id, channel, interval_start):
        return None
    verdict = await _seams.refresh_spam_status(account_id)
    if verdict.status != "clean":
        return None
    rounds = max(2, nc.channel_max_rounds)
    # One window per round, as many rounds as the budget: the same 48h the re-join and
    # join-request rules count out, derived so the operator tunes all of them in one place.
    window = timedelta(hours=nc.channel_pause_hours * rounds)
    failures = await stamp_unconfirmed_ban(
        account_id, channel, (now - window).isoformat(), interval_start
    )
    if not failures:
        # Nothing was counted: the pair has no readiness row, or a rival refusal took the
        # interval between the read above and this write. There is no position in a budget
        # to report ("0/2" would be a counter that never moves) — and, just as important,
        # no cooldown to park the pair with. An unconditional one parked it for a whole
        # ``channel_pause_hours`` over a refusal this rule had charged to nobody.
        return None
    # After the count, never before it: this deadline is what stops the pair being selected
    # again until the interval it shares with the stamp has run out.
    await _state.set_cooldown(account_id, now + pause, channel)
    if failures < rounds:
        return f"{failures}/{rounds}"
    return await _ban_on_a_spent_budget(
        account_id, channel, known_state=known_state, failures=failures, rounds=rounds
    )


async def _ban_on_a_spent_budget(
    account_id: str,
    channel: str,
    *,
    known_state: str | None,
    failures: int,
    rounds: int,
) -> str | None:
    """Take the confirmed ban's exit on the last counted refusal, once @SpamBot re-confirms.

    The one place this rule pays for a FRESH reading, and it pays because this is the
    irreversible step. Every reading before it is cached, which is right for a count a
    delivered comment can undo — but the cache lives ``spam_status_ttl_hours`` (36h) while
    two counted refusals are only ``channel_pause_hours`` (24h) apart, so a verdict stamped
    less than 12h before the first refusal is still served, unrefreshed, to the second. An
    account Telegram limited in between would read as clean and lose this chat for good —
    and, since the count runs per channel, every other chat it posts to that day. One probe
    per pair per budget closes that window; a limited account keeps its position (nothing is
    spent back) and is re-judged on its next refusal.

    ``neurocomment_account_banned``, not ``neurocomment_group_ban_confirmed``: for the pair
    the outcome IS a ban in this channel, and the feed should not need a second word for it,
    but nothing confirmed this group did it. ``reason`` closes the run of positions the
    earlier refusals printed — the feed reads "1/2" then "2/2" rather than "1/2" then
    silence — and it is literally the budget, because this branch is "at or over it" and an
    over-run must not render as "3/2".
    """
    if (await _seams.refresh_spam_status(account_id, force=True)).status != "clean":
        await log_event(
            "WARNING",
            "neurocomment_group_ban_account_limited",
            account_id=account_id,
            extra={"channel": channel, "state": known_state, "unconfirmed_bans": failures},
        )
        return None
    spent = f"{rounds}/{rounds}"
    await _mark_banned_and_leave(
        account_id,
        channel,
        "neurocomment_account_banned",
        unconfirmed_bans=failures,
        reason=spent,
    )
    return spent


async def _mark_banned_and_leave(
    account_id: str,
    channel: str,
    event: str,
    **extra: object,
) -> None:
    """Park the pair here for good and walk out of the chat — the ban's one exit.

    Shared by the two verdicts that reach it (Telegram confirmed the group banned us, or
    the group refused us until the budget ran out) so they cannot drift: the same writes in
    the same order, the same best-effort leave, the same unlink check. Only the event code
    and any extra fields differ, because only the REASON differs.
    """
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
        event,
        account_id=account_id,
        extra={"channel": channel, "leave": outcome, **extra},
    )
    await _unlink_channel_if_no_account_left(account_id, channel)


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
    the other accounts only after their own 48h have run out, whereas one ban
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
