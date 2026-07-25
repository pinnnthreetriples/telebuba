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

from typing import TYPE_CHECKING

from core import db
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
    DiscoveryProgress,
    DiscoverySearchOutcome,
)
from services.neurocomment import _discovery_state, _runtime
from services.neurocomment._discovery_providers import SearchAccount, resolve_search_account
from services.neurocomment._discovery_qualify import run_qualification
from services.neurocomment._discovery_search import run_search
from services.neurocomment._signals import signal_discovery_progress

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

    ``None`` for an unknown campaign, as in ``load_discovery``. Checked first and
    against the campaign, not the account: ``resolve_search_account`` falls back to
    the global listener, so without this a deleted campaign would answer "started",
    spend a search slot and real RPCs, then die on the foreign key.

    Refusals are statuses, not exceptions, so the API can report them verbatim:
    another run in flight, no usable account, that account cooling off, or the
    rolling-24h search allowance spent.
    """
    if await db.fetch_campaign(campaign_id) is None:
        return None
    account = await resolve_search_account(campaign_id)
    if isinstance(account, str):
        return DiscoverySearchOutcome(status=account)  # ty: ignore[invalid-argument-type]

    # Resolution first, then one synchronous claim: everything from here to ``spawn``
    # is await-free, so a second start cannot straddle it and no failure can strand a
    # claim. The claim covers the account too, not just the campaign — every campaign
    # resolves to the same listener.
    refusal = _discovery_state.try_reserve(campaign_id, account.account_id)
    if refusal is not None:
        return DiscoverySearchOutcome(status=refusal)

    _discovery_state.set_phase(campaign_id, "searching")
    _discovery_state.set_last_error(campaign_id, None)
    _discovery_state.spawn(campaign_id, _run(campaign_id, account, request))
    await log_event(
        "INFO",
        "neurocomment_discovery_started",
        extra={
            "campaign_id": campaign_id,
            "account_id": account.account_id,
            "keywords": len(request.keywords),
            "use_telemetr": request.use_telemetr,
        },
    )
    return DiscoverySearchOutcome(status="started")


async def _run(
    campaign_id: str,
    account: SearchAccount,
    request: DiscoverySearchRequest,
) -> None:
    """The background run: search, then qualify. Never lets an error escape."""
    try:
        found, search_error, replaced = await run_search(
            campaign_id,
            account.account_id,
            request,
        )
        _discovery_state.set_last_error(campaign_id, search_error)
        qualify_error = None
        if not replaced:
            # No source answered, so the stored candidates are still the previous run's
            # and this is not a run the operator should read as done. Keyed off the
            # write, not off ``(found, error)``: a source that answered with zero hits,
            # or a filter that removed every hit, is an empty result — not a failure.
            _discovery_state.set_phase(campaign_id, "failed")
        else:
            _discovery_state.set_phase(campaign_id, "qualifying")
            signal_discovery_progress()

            qualify_error = await run_qualification(campaign_id, account.account_id)
            if qualify_error is not None:
                _discovery_state.set_last_error(campaign_id, qualify_error)
                _discovery_state.set_phase(campaign_id, "failed")
            else:
                _discovery_state.set_phase(campaign_id, "done")
        await log_event(
            "INFO",
            "neurocomment_discovery_finished",
            extra={
                "campaign_id": campaign_id,
                "found": found,
                "reason": qualify_error or search_error,
            },
        )
    except Exception as exc:  # noqa: BLE001 - a background task must not die silently
        _discovery_state.set_phase(campaign_id, "failed")
        _discovery_state.set_last_error(campaign_id, type(exc).__name__)
        await log_event(
            "ERROR",
            "neurocomment_discovery_failed",
            extra={"campaign_id": campaign_id, "reason": type(exc).__name__},
        )
    finally:
        signal_discovery_progress()


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

    candidates: list[DiscoveryCandidate] = []
    comments_on = 0
    qualified = 0
    for row in rows:
        qualification = _qualification(row, comments)
        qualified += qualification != "pending"
        comments_on += qualification == "comments_on"
        owner = owners.get(row.channel)
        owner_id = None if owner is None else owner.campaign_id
        candidates.append(
            DiscoveryCandidate(
                channel=row.channel,
                title=row.title,
                subscribers=row.subscribers,
                source=row.source,
                qualification=qualification,
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
        ),
        candidates=candidates,
    )


async def adopt_candidates(campaign_id: str, channels: list[str]) -> DiscoveryAdoptResult | None:
    """Link the operator's picks to the campaign, reporting one outcome per channel.

    Every refusal AND every failure is a per-channel status, never an exception:
    ``already_assigned`` (the channel is another campaign's active target) and
    ``failed`` (the attempt raised). One bad channel must not cost the report for the
    other 29 — those stay linked either way, so aborting the batch left the operator
    with an opaque 500 and no way to know what had already happened.

    No whole-batch abort, not even for a systemic cause (campaign deleted mid-loop, DB
    locked): every link is its own transaction, so the systemic case simply degrades
    into a full list of ``failed`` the operator can retry from, while the common
    transient case costs exactly one channel instead of the whole selection.

    The listener reconcile fires ONCE at the end, and only if something linked:
    adopting 30 channels must not trigger 30 reconciles.
    """
    campaign = await db.fetch_campaign(campaign_id)
    if campaign is None:
        return None

    outcomes: list[DiscoveryAdoptOutcome] = []
    linked = 0
    failures = 0
    reason: str | None = None
    for channel in channels:
        try:
            await db.link_channel_to_campaign(campaign_id, channel)
        except db.ChannelAlreadyAssignedError:
            outcomes.append(DiscoveryAdoptOutcome(status="already_assigned", channel=channel))
            continue
        except Exception as exc:  # noqa: BLE001 - reported per channel, see the docstring
            failures += 1
            reason = reason or type(exc).__name__
            outcomes.append(DiscoveryAdoptOutcome(status="failed", channel=channel))
            continue
        linked += 1
        outcomes.append(DiscoveryAdoptOutcome(status="linked", channel=channel))

    if linked:
        # Reached even when a later channel failed, or a running listener would ignore the
        # ones that did link until the next restart. Cancellation is a ``BaseException``,
        # so it unwinds past this: a cancelled request must not start a reconcile it would
        # only delay, and a second cancel would tear that reconcile in half.
        await _runtime.reconcile_if_running()
    await log_event(
        "INFO",
        "neurocomment_discovery_adopted",
        extra={
            "campaign_id": campaign_id,
            "linked": linked,
            "submitted": len(channels),
            "failed": failures,
            # First failure only: one short code, like the search stage's degraded reason.
            "reason": reason,
        },
    )
    return DiscoveryAdoptResult(outcomes=outcomes)
