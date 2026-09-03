"""The background discovery run — search, then qualify. Never lets an error escape.

Split from ``discovery`` (file-size cap), which keeps the start gate, the board and
adopt. A background task that raised into the void would leave the campaign's phase
stuck forever, so the whole run is one guarded coroutine.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.logging import log_event
from core.repositories.neurocomment import list_discovery_candidates, mark_seen
from services.neurocomment import _discovery_state
from services.neurocomment._discovery_qualify import run_qualification
from services.neurocomment._discovery_search import run_search
from services.neurocomment._signals import signal_discovery_progress

if TYPE_CHECKING:
    from schemas.neurocomment_discovery import DiscoverySearchRequest
    from services.neurocomment._discovery_pool import AccountPool


async def run(campaign_id: str, pool: AccountPool, request: DiscoverySearchRequest) -> None:
    try:
        stage = await run_search(campaign_id, pool, request)
        _discovery_state.set_last_error(campaign_id, stage.error)
        # Published whether or not the rows were stored: a run that stored nothing is
        # exactly when the operator needs to see which source refused. The search stage
        # has already stripped everything that would describe rows nobody can see.
        _discovery_state.set_run_report(campaign_id, stage.report)
        qualify_error = None
        if not stage.replaced or stage.flooded or pool.empty:
            # Not replaced: no source answered (or the filter-aware one did not), so the
            # stored candidates are still the previous run's and this is not a run the
            # operator should read as done. Keyed off the write, not off ``(found,
            # error)``: a source that answered with zero hits, or a filter that removed
            # every hit, is an empty result — not a failure.
            # Flooded: a rate limit is in force on the last search account — the search
            # stage either caused it and wrote the cooldown, or found one somebody else
            # recorded. Qualifying would fire getFullChannel straight into the live
            # window, which is how Telegram turns a soft limit into a hard one.
            # An empty pool: every account left the rotation, so there is nothing to
            # probe with either.
            _discovery_state.set_phase(campaign_id, "failed")
        else:
            _discovery_state.set_phase(campaign_id, "qualifying")
            signal_discovery_progress()

            qualify_error = await run_qualification(campaign_id, pool, request)
            # Shown once is shown: ``hide_seen`` on the next search drops these. Marked
            # AFTER qualification, over the rows the board actually shows — a row a
            # probe-time filter deleted was never shown, and marking the search stage's
            # whole set hid such channels from every later search for good. Settled rows
            # only: a pass the pool's emptying stopped early leaves the rest pending, and
            # marking those hid them from the very re-run that would have resumed them.
            rows = (await list_discovery_candidates(campaign_id)).rows
            settled = (row.channel for row in rows if row.qualified_at is not None)
            await mark_seen(settled, datetime.now(UTC))
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
                "found": stage.found,
                "reason": qualify_error or stage.error,
                # Per source, so a run that reached "done" with a filter that never
                # applied is visible in the log too, not only on the board — including
                # the gateway's diagnostic text, which the short reason cannot carry.
                "sources": [
                    report.model_dump(exclude_none=True) for report in stage.report.sources
                ],
                "filtered": _discovery_state.run_report(campaign_id).filtered,
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
