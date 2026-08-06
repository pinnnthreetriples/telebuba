"""The rule for an account a guardian bot will not let speak: one more try, then walk out.

A pair the solver loses to is left holding ``(joined=1, captcha_passed=0, ready=0)``, and
that triple matches NONE of ``_onboard_pair._join_and_classify``'s guards — not the skip,
not the ban, not the join-request wait, not ``_rejoin``'s back-off. So every onboarding
trigger already re-ran the solver on it, for free and forever: an operator Start, a boot, a
campaign reconcile, another channel's poke. What was missing is not the retry. It is a
BOUNDED, TIMED one and a terminal state, which is all this module adds.

Modelled field for field on its sibling :mod:`services.neurocomment._rejoin` — same sweep
tick, same poke-don't-join discipline, same channel-pause exemption, same coverage rule
before a channel is dropped. Read that module first; the four decisions that DIFFER, and
why, are these:

* **The budget is fixed at one retry, not ``channel_max_rounds``.** The pair's first
  attempt is the onboarding solve, which this rule never spends and never sees; it grants
  the SECOND. The operator's rule is "the solver gets one more go, then the account leaves",
  so a knob would make the shipped behaviour a coincidence. The 2/2 the log carries counts
  both, which is what the operator is actually watching.
* **A persisted stamp is the budget, not a row count.** ``neurocomment_challenges`` looks
  like the natural counter and is not one: it counts every TRIGGER's attempts (three
  operator Starts and the pair is finished on the spot), and it never moves at all for a
  pair the poked pass cannot reach — an account at its rolling-24h join cap, a failing
  ``_safe_resolve``, an LLM 429 that writes no row. That is verbatim the defect ``_rejoin``'s
  docstring says a budget must not have. The table is used to IDENTIFY a captcha-blocked
  pair (see ``_captcha_failed_since``); ``captcha_retry_at`` is the budget.
* **The terminal state is a LEAVE, not a park.** ``_rejoin`` gives up on a pair that is
  already out of the chat; this one gives up on a pair that is still sitting in a group it
  cannot speak in, so it walks out (``_give_up_and_leave``). Nothing re-joins it: the
  ``captcha_gave_up`` column is what ``_join_and_classify`` refuses on from then on.
* **There is no staleness rule.** ``_rejoin._stamp_stale`` exists because its budget is a
  COUNTER a shrinking ``channel_max_rounds`` can strand above the cap. One boolean stamp
  against a fixed budget of one cannot be stranded by any setting, so the only clock here
  is the unanswered-poke floor in :func:`retry_spent`.

No human reaches this path at all: the «Повторить» button, ``retry_pair`` and its route were
deleted in the same change, so every branch below runs unattended — the whole requirement.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.db import (
    fetch_active_campaigns_for_channels,
    fetch_channel_paused_until,
    list_campaign_accounts,
    list_captcha_blocked_readiness,
    list_channel_readiness,
    mark_captcha_gave_up,
    stamp_captcha_retry,
)
from core.logging import log_event
from schemas.telegram_actions import LeaveDiscussionGroup
from services.neurocomment import _seams, _state
from services.neurocomment._pins import serving_accounts

if TYPE_CHECKING:
    from schemas.neurocomment import NeurocommentReadiness

# Both attempts the operator counts: the onboarding solve that lost, and the one re-solve
# this rule authorises. A constant rather than ``channel_max_rounds`` — see the module
# docstring — and named so the two log lines cannot print different budgets.
_ATTEMPTS = 2


def captcha_blocked(readiness: NeurocommentReadiness) -> bool:
    """True for a pair sitting in a group whose guardian bot will not let it speak.

    Field for field the SQL predicate in ``_captcha_giveup._CAPTCHA_BLOCKED``, the pairing
    ``_rejoin.access_lost`` ↔ ``_readiness._ACCESS_LOST`` sets the precedent for (a test
    pins the two in step). Including the three exclusions, and for their reason: onboarding
    refuses a skipped (#148), banned (#30) or already-given-up pair, so counting one as
    blocked would leave a pair that is due forever and poke onboarding every five minutes
    for a solve that never runs.

    NOT a claim that a captcha is the wall — this triple is also what ``_classify``'s
    ``_GATE_ERRORS`` branch writes for an admin mute. The challenge row the bulk read joins
    on is the discriminator; this predicate is only its readiness half.
    """
    return (
        readiness.joined
        and not readiness.captcha_passed
        and not readiness.ready
        and not readiness.banned
        and not readiness.human_skipped
        and not readiness.captcha_gave_up
    )


def retry_owed(readiness: NeurocommentReadiness) -> bool:
    """True while this pair's one re-solve has not been authorised yet.

    The whole budget, and it is deliberately not derived from ``channel_max_rounds`` the way
    the three sibling rules derive theirs. Those count attempts they THEMSELVES spend; the
    first of this pair's two is the onboarding solve, which happened before this rule ever
    saw the pair and which it can neither count nor repeat. So the setting would be
    describing something else, and tuning it would silently change a rule the operator
    stated as an absolute: one more try, then out.
    """
    return readiness.captcha_retry_at is None


def retry_spent(readiness: NeurocommentReadiness, now: datetime) -> bool:
    """True once the authorised re-solve has had its chance and the pair is still blocked.

    Two ways for that to be true, and both are needed:

    * A pass ANSWERED it. Every onboarding pass writes ``checked_at``, so a readiness write
      newer than the stamp is a re-solve that has come back — and the caller only asks this
      of pairs that are still ``captcha_blocked``, so coming back means it lost again. This
      is ``_rejoin.attempt_owed`` negated, and it exists for that function's reason: an
      onboarding pass takes minutes (resolve, jittered join, the solver's own timeouts), and
      giving up on the next 5-minute tick would kill a retry still in flight.
    * Nobody answered it for a whole ``channel_pause_hours``. The poke is best-effort — the
      account may be at its rolling-24h join cap, the resolve may keep failing, the campaign
      may have been stopped — and without a floor such a pair would hold its channel open
      forever on a retry nothing was ever going to run. The same window every sibling rule
      waits out, so the operator tunes one number.

    Never true before the stamp: an unauthorised pair is owed a retry, not out of them.
    """
    if readiness.captcha_retry_at is None:
        return False
    stamped = datetime.fromisoformat(readiness.captcha_retry_at)
    if datetime.fromisoformat(readiness.checked_at) > stamped:
        return True
    return now - stamped >= timedelta(hours=settings.neurocomment.channel_pause_hours)


async def review_captcha_blocked(now: datetime) -> None:
    """Age the captcha-blocked pairs: authorise the owed re-solves, retire the spent ones.

    Never raises — a failure here must not abort the deletion sweep that owns this tick.
    """
    # The same 48h the three sibling rules count out, derived from the two shipped settings
    # rather than added as a knob of its own: a challenge failure older than the whole
    # timeline belongs to a settled episode, and the table is append-only, so an unbounded
    # read would hand a pair from months ago a brand-new retry.
    nc = settings.neurocomment
    since = (now - timedelta(hours=nc.channel_pause_hours * nc.channel_max_rounds)).isoformat()
    try:
        rows = (await list_captcha_blocked_readiness(since)).readiness
    except Exception as exc:  # noqa: BLE001 - the review must never abort the sweep loop.
        await log_event(
            "WARNING",
            "neurocomment_captcha_review_failed",
            extra={"error_type": type(exc).__name__},
        )
        return
    by_channel: dict[str, list[NeurocommentReadiness]] = defaultdict(list)
    for row in rows:
        by_channel[row.channel].append(row)
    # Only channels still linked to an ACTIVE campaign can be dropped; the bulk read also
    # hands us the campaign id ``deactivate_channel`` needs.
    campaigns = await fetch_active_campaigns_for_channels(list(by_channel))
    retry_authorised_somewhere = False
    for channel, channel_rows in by_channel.items():
        campaign = campaigns.get(channel)
        if campaign is None:
            continue
        if await _review_channel(campaign.campaign_id, channel, channel_rows, now):
            retry_authorised_somewhere = True
    if retry_authorised_somewhere:
        # Late import: ``_runtime`` reaches this module through the sweep, so a top-level
        # import cycles. Same poke ``_rejoin`` uses — onboarding, not this pass, re-joins
        # and re-solves, because it owns the join cap and the jitter.
        from services.neurocomment import _runtime, _signals  # noqa: PLC0415

        _runtime._ensure_onboarding_running(_signals.signal_onboarding_progress)  # noqa: SLF001


async def _review_channel(
    campaign_id: str,
    channel: str,
    channel_rows: list[NeurocommentReadiness],
    now: datetime,
) -> bool:
    """Age one channel's blocked pairs; True when a re-solve was authorised this tick.

    Its own function so the caller stays a flat loop over channels — and because the three
    outcomes here (authorise, wait, retire) are the whole rule.
    """
    # Only pairs an onboarding pass can actually reach: it walks the campaign's serving
    # accounts, pin-aware. A row left behind by an account since removed from the campaign,
    # or pinned to other channels, is nobody's to retry — authorising one would stamp a
    # budget against a solve that never runs and then give up on the pair for losing it.
    serving = serving_accounts((await list_campaign_accounts(campaign_id)).links, channel)
    blocked = [row for row in channel_rows if row.account_id in serving and captcha_blocked(row)]
    if not blocked:
        return False
    # The one thing the stamp-first design must not charge for, and ``_rejoin`` documents
    # why at length: ``_join_and_classify`` returns ``channel_paused`` BEFORE any join RPC,
    # so during a #147 window the poke reaches nothing at all and the stamp would burn the
    # whole budget against a channel nobody could even try. Read off the same column
    # onboarding refuses on, so the two cannot disagree. The give-up waits too — a budget
    # spent against refused joins is no evidence. Deferred, never waived: a pause window is
    # a flat ``channel_pause_hours``, so the timeline resumes on the first tick after it.
    if _state.channel_paused(await fetch_channel_paused_until(channel), now):
        return False
    # Authorise and retire in the SAME tick, unlike ``_rejoin``, which returns after its
    # stamps. Both of its outcomes are channel-wide (poke, or unlink), so ordering them
    # matters there; only one of these is. Retiring a pair whose own retry came back and
    # lost is a verdict about that pair alone, and holding it back because a SIBLING was
    # just handed its first retry would keep a finished pair in the chat for another
    # ``channel_pause_hours`` per sibling ahead of it in the queue. The two lists cannot
    # overlap — ``retry_owed`` wants no stamp and ``retry_spent`` wants one.
    due = [row for row in blocked if retry_owed(row)]
    if due:
        for row in due:
            await stamp_captcha_retry(row.account_id, channel)
            # The operator's only sight of this rule while it runs. ``reason`` carries the
            # position in the budget rather than a code: nothing in ``logEventReason``
            # matches "2/2" and ``eventReason`` renders an unmapped code raw, which is what
            # puts "· 2/2" beside the label. Always 2/2 — this rule authorises the pair's
            # SECOND and last attempt, so there is no 1/2 for it to print.
            await log_event(
                "INFO",
                "neurocomment_captcha_retry",
                account_id=row.account_id,
                extra={
                    "channel": channel,
                    "attempts": _ATTEMPTS,
                    "reason": f"{_ATTEMPTS}/{_ATTEMPTS}",
                },
            )
    finished = [row for row in blocked if retry_spent(row, now)]
    for row in finished:
        await _give_up_and_leave(row.account_id, channel)
    if len(finished) == len(blocked):
        # Only now is the channel's fate settled. Any shortfall means somebody is still
        # mid-timeline — stamped this tick, or stamped earlier and neither answered nor out
        # of window — and unlinking on that would cancel a re-solve still in flight, the trap
        # ``_rejoin.still_retrying`` was written for. It also covers the ``due`` pairs above
        # without naming them: a pair authorised a moment ago is never ``retry_spent``.
        await _drop_channel_if_nobody_passed(campaign_id, channel, serving, finished)
    return bool(due)


async def _give_up_and_leave(account_id: str, channel: str) -> None:
    """Stop trying to pass this chat's captcha and walk out of the chat.

    A SIBLING of ``bans._mark_banned_and_leave``, not a reuse of it and not an extraction
    from it: that function's five steps include a ban-shaped ``upsert_readiness``,
    ``mark_pair_banned`` and ``_unlink_channel_if_no_account_left``, three of which are
    wrong here — this pair is not banned, and the channel drop belongs to this rule's own
    clock, one call up. Only the leave and the WARNING are genuinely shared, and folding
    them out would put this rule's import into ``bans.py``, which the file-size gate has no
    room for. Read that function too if you change either: the ORDER below is its rule.

    The verdict is persisted FIRST and the leave is best-effort after it, exactly as the ban
    does it: ``captcha_gave_up`` is the truth and has to survive a leave RPC that dies, or
    the next onboarding pass re-solves a pair we have already given up on. A failing leave
    leaves us a silent member of a group we never write in, which is the cheap direction.
    """
    await mark_captcha_gave_up(account_id, channel)
    try:
        leave = await _seams.execute(account_id, LeaveDiscussionGroup(channel=channel))
        outcome = leave.status
    except Exception as exc:  # noqa: BLE001 - the verdict stands; the leave is best-effort.
        outcome = type(exc).__name__
    await log_event(
        "WARNING",
        "neurocomment_captcha_gave_up",
        account_id=account_id,
        extra={
            "channel": channel,
            "leave": outcome,
            "attempts": _ATTEMPTS,
            "reason": f"{_ATTEMPTS}/{_ATTEMPTS}",
        },
    )


async def _drop_channel_if_nobody_passed(
    campaign_id: str,
    channel: str,
    serving: list[str],
    gave_up: list[NeurocommentReadiness],
) -> None:
    """Unlink a channel whose captcha every serving account has now given up on.

    The coverage rule of ``_rejoin._drop_channel_if_nothing_works`` verbatim, and for its
    reason: a serving account with NO readiness row was never tried here, not tried and
    failed, and onboarding reaches a fleet slowly — so every serving account must have a
    row, and any ``ready`` one keeps the channel. Re-read rather than counted off the rows
    the caller already holds, because those are only the pairs THIS rule finished with.
    """
    rows = (await list_channel_readiness(campaign_id, channel, serving)).readiness
    if len(rows) != len(serving) or any(row.ready for row in rows):
        return
    # Via the service, not the repository, so the listener reconciles and stops watching the
    # channel — exactly like the three sibling rules.
    from services.neurocomment import campaigns as campaigns_service  # noqa: PLC0415

    await campaigns_service.deactivate_channel(campaign_id, channel)
    await log_event(
        "WARNING",
        "neurocomment_channel_captcha_unsolved",
        extra={
            "channel": channel,
            "campaign_id": campaign_id,
            "gave_up_accounts": len(gave_up),
            "reason": "captcha_unsolved",
        },
    )
