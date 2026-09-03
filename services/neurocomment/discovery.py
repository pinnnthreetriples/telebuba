"""Automated channel discovery for a campaign — start a run, read it, adopt from it.

The whole run is background. Even the search stage is ~11 paced Telegram reads
(~20s), and qualification is minutes for a full candidate set, so the API validates
and spawns, then the SPA follows the board's ``phase``/``running`` fields over SSE.

Progress is reported the way onboarding already does it — a read model plus
transient SSE nudges — so there is no job registry to reconcile on restart. A run
lost to a restart costs nothing: the candidate rows persist and the linked-group
cache makes the retry nearly free.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core import db
from core.config import settings
from core.logging import log_event
from core.repositories.neurocomment import (
    fetch_active_campaigns_for_channels,
    list_discovery_candidates,
    list_linked_groups,
)
from schemas.neurocomment_discovery import (
    DiscoveryAdoptOutcome,
    DiscoveryAdoptResult,
    DiscoveryBoard,
    DiscoveryCandidate,
    DiscoveryChannelVerdict,
    DiscoveryProgress,
    DiscoveryRunReport,
    DiscoverySearchOutcome,
)
from services.neurocomment import _discovery_state, _runtime
from services.neurocomment._discovery_filters import is_private_ref
from services.neurocomment._discovery_pool import AccountPool, check_search_accounts, taken_account
from services.neurocomment._discovery_qualify import is_fresh
from services.neurocomment._discovery_run import run
from services.neurocomment._discovery_waves import sweep_keywords

if TYPE_CHECKING:
    from schemas.neurocomment import LinkedGroupList
    from schemas.neurocomment_discovery import (
        DiscoveryCandidateRow,
        DiscoveryQualification,
        DiscoverySearchRequest,
    )


async def start_discovery(
    campaign_id: str,
    request: DiscoverySearchRequest,
) -> DiscoverySearchOutcome | None:
    """Validate, then spawn one background run for this campaign.

    ``None`` for an unknown campaign, as in ``load_discovery``. Checked first: without
    this a deleted campaign would answer "started", spend a search slot and real RPCs,
    then die on the foreign key.

    Refusals are statuses, not exceptions, so the API can report them verbatim:
    another run in flight, a picked account that is unknown, busy or cooling off (named
    in ``refused_account_id``, first in id order), or the rolling-24h search allowance
    spent.
    """
    if await db.fetch_campaign(campaign_id) is None:
        return None
    # Ahead of the account check: this campaign's own run holds its accounts, and that
    # must read as "already running", not as an account another campaign took.
    if _discovery_state.is_running(campaign_id):
        return DiscoverySearchOutcome(status="already_running")
    # Sorted, here and for the locks below: two starts picking overlapping accounts must
    # take the locks in one order, or they deadlock each other.
    account_ids = sorted(request.account_ids)
    accounts = await check_search_accounts(campaign_id, account_ids)
    if isinstance(accounts, DiscoverySearchOutcome):
        return accounts

    # Checks first, then one synchronous claim: everything from ``try_reserve`` to
    # ``spawn`` is await-free, so a second start cannot straddle it and no failure can
    # strand a claim. The claim covers the accounts too, not just the campaign.
    #
    # Under warming's own per-account lifecycle lock for every picked account, re-asking
    # every holder inside it, for the reason ``start_neurocomment`` takes the same lock:
    # ``check_search_accounts`` answered several awaits ago, and ``start_warming`` and the
    # listener start both read this claim under that lock before they commit. Without it
    # their checks and this one all pass in the gap and the account ends up carrying two
    # paced streams. The spawned task cannot start before this coroutine next yields,
    # which is after the locks are released, so they never span the run itself.
    from services.warming import account_lock  # noqa: PLC0415 - avoid a load-time cycle.

    async with contextlib.AsyncExitStack() as locks:
        for account_id in account_ids:
            await locks.enter_async_context(account_lock(account_id))
        taken = await taken_account(campaign_id, account_ids)
        if taken is not None:
            return DiscoverySearchOutcome(status="account_busy", refused_account_id=taken)
        refusal = _discovery_state.try_reserve(campaign_id, frozenset(account_ids))
        if refusal is not None:
            return DiscoverySearchOutcome(status=refusal)

        _discovery_state.set_phase(campaign_id, "searching")
        _discovery_state.set_last_error(campaign_id, None)
        # Synchronous, with the rest of this reset: ``start_work`` only replaces the
        # tracker once the spawned task reaches its first stage, several awaits later —
        # without this a board poll in between paired the NEW phase with the PREVIOUS
        # run's live streams.
        _discovery_state.clear_work(campaign_id)
        # Per-run state, so it is cleared where the rest of it is. A verdict describes the
        # channel a PREVIOUS run saw — a channel this run does not find again would keep
        # it, and the map would grow for the life of the process.
        _discovery_state.clear_verdicts(campaign_id)
        # The source strip is per-run for the same reason, and the run's exception path
        # never sets one: without this, a run that crashed published the PREVIOUS run's
        # strip beside its own failure. The empty report has stored nothing, so until the
        # search stage writes, the board reports the rows it shows as the previous run's.
        _discovery_state.set_run_report(campaign_id, DiscoveryRunReport())
        _discovery_state.spawn(campaign_id, run(campaign_id, AccountPool(accounts), request))
    await log_event(
        "INFO",
        "neurocomment_discovery_started",
        extra={
            "campaign_id": campaign_id,
            "account_ids": account_ids,
            # The sweep as run: the typed words plus the category's bundle, deduped.
            "keywords": len(sweep_keywords(request)),
        },
    )
    return DiscoverySearchOutcome(status="started")


def _qualification(
    row: DiscoveryCandidateRow,
    comments: dict[str, bool],
) -> DiscoveryQualification:
    if row.qualified_at is None:
        return "pending"
    if row.qualify_error is not None:
        # Probed but unanswerable — distinct from "not probed yet" so the operator
        # can tell "wait" from "we could not tell".
        return "unknown"
    enabled = comments.get(row.channel)
    if enabled is None:
        return "unknown"
    return "comments_on" if enabled else "comments_off"


def _comments_map(cached: LinkedGroupList) -> dict[str, bool]:
    return {group.channel: bool(group.comments_enabled) for group in cached.groups}


async def load_discovery(campaign_id: str) -> DiscoveryBoard | None:
    """The candidate list plus run progress, or ``None`` for an unknown campaign."""
    campaign = await db.fetch_campaign(campaign_id)
    if campaign is None:
        return None

    rows = (await list_discovery_candidates(campaign_id)).rows
    channels = [row.channel for row in rows]
    comments = _comments_map(await list_linked_groups(channels))
    # One bulk read answers both membership flags; the partial unique index means a
    # channel active elsewhere cannot be adopted here.
    owners = await fetch_active_campaigns_for_channels(channels)

    report = _discovery_state.run_report(campaign_id)
    verdicts = _discovery_state.verdicts(campaign_id)
    candidates: list[DiscoveryCandidate] = []
    comments_on = 0
    qualified = 0
    for row in rows:
        qualification = _qualification(row, comments)
        qualified += qualification != "pending"
        comments_on += qualification == "comments_on"
        owner = owners.get(row.channel)
        owner_id = None if owner is None else owner.campaign_id
        # Provenance and the fitness verdict are only known while the run's state lives;
        # a board read after a restart falls back to the one source the row itself stores
        # — verbatim, whatever build wrote it — and to no verdict at all, which the wire
        # model documents as unknown. The three probe-derived facts are lifted off the
        # verdict for the same reason: nothing else holds them.
        origin = report.origins.get(row.channel)
        verdict = verdicts.get(row.channel)
        facts = verdict or DiscoveryChannelVerdict()
        candidates.append(
            DiscoveryCandidate(
                channel=row.channel,
                title=row.title,
                subscribers=row.subscribers,
                source=row.source,
                kind=row.kind,
                access=facts.access,
                language=facts.language,
                category_match=facts.category_match,
                sources=[row.source] if origin is None else list(origin.sources),
                qualification=qualification,
                uncounted=origin is not None and origin.uncounted,
                verdict=verdict,
                in_campaign=owner_id == campaign_id,
                taken_by_other_campaign=owner_id is not None and owner_id != campaign_id,
            ),
        )

    return DiscoveryBoard(
        campaign_id=campaign_id,
        progress=DiscoveryProgress(
            phase=_discovery_state.phase_of(campaign_id),
            running=_discovery_state.is_running(campaign_id),
            total=len(rows),
            qualified=qualified,
            comments_on=comments_on,
            last_error=_discovery_state.last_error(campaign_id),
            sources=report.sources,
            # The rows below outlived the last run — it stored nothing, has not stored
            # yet (still searching), or predates this process — so they are the previous
            # search's and the board must not present them as this one's find. No rows,
            # nothing to be stale.
            stale_candidates=bool(rows) and not report.stored,
            capped=report.capped,
            filtered=report.filtered,
            work=_discovery_state.work(campaign_id),
        ),
        candidates=candidates,
    )


async def _comments_off_channels(channels: list[str]) -> set[str]:
    """Of these, the ones a CURRENT persisted verdict says have comments switched off.

    The linked-group cache is the durable half of the qualification — the only half that
    survives a restart — so it is the only half a server-side guard may act on. The
    richer in-memory verdict deliberately does NOT feed this: gating on state that
    evaporates with the process would make the same request succeed or fail depending on
    the server's uptime.

    Everything short of an explicit, still-fresh "off" adopts. A channel with no cache
    row was never probed, and a stale row may well have switched comments on since — the
    board itself re-probes those — so neither is a refusal. A cold cache must never
    become a way to block adoption.
    """
    now = datetime.now(UTC)
    cached = await list_linked_groups(channels)
    return {
        group.channel
        for group in cached.groups
        if not group.comments_enabled and is_fresh(group.checked_at, now)
    }


async def adopt_candidates(campaign_id: str, channels: list[str]) -> DiscoveryAdoptResult | None:
    """Link the operator's picks to the campaign, reporting one outcome per channel.

    Every refusal AND every failure is a per-channel status, never an exception:
    ``already_assigned`` (the channel is another campaign's active target),
    ``comments_off`` (its cached verdict says the campaign could never comment there),
    ``not_adoptable`` (a group, or a channel with no public handle — nothing a campaign
    could comment in, so no link is attempted) and ``failed`` (the attempt raised). One
    bad channel must not cost the report for the other 29 — those stay linked either way,
    so aborting the batch left the operator with an opaque 500 and no way to know what
    had already happened.

    The comments check is here and not only in the SPA: the UI merely disables those
    checkboxes, so any caller that skipped it linked a channel the campaign can never
    comment in, and the two surfaces disagreed with only the client enforcing anything.

    A transient failure costs exactly one channel, because every link is its own
    transaction. A systemic one (campaign deleted mid-loop, DB wedged) stops the loop
    after ``discovery_max_consecutive_errors`` in a row — 500 doomed writes buy nothing
    — and the untried remainder is reported ``failed`` too, so the operator still gets
    one outcome per channel they asked for.

    The listener reconcile fires ONCE at the end, and only if something linked:
    adopting 30 channels must not trigger 30 reconciles.
    """
    campaign = await db.fetch_campaign(campaign_id)
    if campaign is None:
        return None

    outcomes: list[DiscoveryAdoptOutcome] = []
    linked = 0
    failures = 0
    refused = 0
    reason: str | None = None
    consecutive = 0
    comments_off = await _comments_off_channels(channels)
    rows = (await list_discovery_candidates(campaign_id)).rows
    groups = {row.channel for row in rows if row.kind == "group"}
    for index, channel in enumerate(channels):
        if consecutive >= settings.neurocomment.discovery_max_consecutive_errors:
            # Nothing is working; stop writing. A refusal resets the counter, so only real
            # failures in a row get here.
            remainder = channels[index:]
            failures += len(remainder)
            outcomes.extend(
                DiscoveryAdoptOutcome(status="failed", channel=rest) for rest in remainder
            )
            break
        if is_private_ref(channel) or channel in groups:
            # Like ``comments_off`` below: no write attempted, counter left alone.
            outcomes.append(DiscoveryAdoptOutcome(status="not_adoptable", channel=channel))
            continue
        if channel in comments_off:
            # No write attempted, so the abort counter is left alone: this says nothing
            # about whether the database is answering.
            refused += 1
            outcomes.append(DiscoveryAdoptOutcome(status="comments_off", channel=channel))
            continue
        try:
            await db.link_channel_to_campaign(campaign_id, channel)
        except db.ChannelAlreadyAssignedError:
            consecutive = 0
            outcomes.append(DiscoveryAdoptOutcome(status="already_assigned", channel=channel))
            continue
        except Exception as exc:  # noqa: BLE001 - reported per channel, see the docstring
            failures += 1
            consecutive += 1
            reason = reason or type(exc).__name__
            outcomes.append(DiscoveryAdoptOutcome(status="failed", channel=channel))
            continue
        consecutive = 0
        linked += 1
        outcomes.append(DiscoveryAdoptOutcome(status="linked", channel=channel))

    if linked:
        # Reached even when a later channel failed, or a running listener would ignore the
        # ones that did link until the next restart. Cancellation is a ``BaseException``,
        # so it unwinds past this: a cancelled request must not start a reconcile it would
        # only delay, and a second cancel would tear that reconcile in half.
        with contextlib.suppress(Exception):
            # The reconcile does settings reads and Telegram work. It is a best-effort
            # nudge for the running listener, not part of the adopt: letting it raise
            # would replace the per-channel report with an opaque 500 (and swallow the
            # log below) while the channels stayed linked regardless.
            await _runtime.reconcile_if_running()
    await log_event(
        "INFO",
        "neurocomment_discovery_adopted",
        extra={
            "campaign_id": campaign_id,
            "linked": linked,
            "submitted": len(channels),
            "failed": failures,
            # Refused on their cached qualification, not attempted.
            "comments_off": refused,
            # First failure only: one short code, like the search stage's degraded reason.
            "reason": reason,
        },
    )
    return DiscoveryAdoptResult(outcomes=outcomes)
