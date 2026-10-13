"""The survival panel and the card badge, read from the lifecycle journal."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.repositories.accounts import list_accounts
from core.repositories.lifecycle import list_all_lifecycle_events
from core.repositories.warming import list_warming_states
from core.repositories.warming_daily_stats import sum_warming_daily_stats_by_day
from schemas.survival import AccountSurvival, SurvivalReport
from services.survival._judging import check_grace, judge_stints
from services.survival._outcomes import stint_facts, stint_status
from services.survival._stints import build_stints
from services.survival._tally import Clock, causes, cohorts, swimmer_row, tally_windows

if TYPE_CHECKING:
    from schemas.accounts import AccountRead
    from schemas.survival import SurvivalCohortKind

_DAY_LENGTH = len("YYYY-MM-DD")


async def load_survival(
    cohort: SurvivalCohortKind = "week",
    *,
    now: datetime | None = None,
) -> SurvivalReport:
    """Totals per window, one cohort cut, causes, swimmer rows and the fleet's history."""
    clock = Clock(now=now or datetime.now(UTC), grace=check_grace())
    events = await list_all_lifecycle_events()
    accounts = {account.account_id: account for account in (await list_accounts()).accounts}
    records = {record.account_id: record for record in await list_warming_states()}
    judged = judge_stints(build_stints(events), accounts, records)
    newest_first = sorted(judged, key=lambda item: item.stint.started_at, reverse=True)
    since_day = (clock.now - timedelta(days=settings.survival.history_days - 1)).date()
    first_day = min(event.created_at for event in events)[:_DAY_LENGTH] if events else None
    return SurvivalReport(
        cohort=cohort,
        windows=tally_windows(judged, clock),
        cohorts=cohorts(judged, cohort, clock),
        causes=causes(judged),
        stints=[swimmer_row(item, clock) for item in newest_first[: settings.survival.stint_rows]],
        releases=sum(1 for item in judged if item.facts.released_at is not None),
        full_data_since=first_day,
        daily=await sum_warming_daily_stats_by_day(since_day.isoformat()),
    )


async def load_account_survivals(
    accounts: list[AccountRead],
    *,
    now: datetime | None = None,
) -> dict[str, AccountSurvival]:
    """The card badge of every released account among ``accounts`` (one journal read).

    Rows older than the live account row are dropped as well as those before a deletion:
    the ``deleted`` row is best-effort, and a re-imported id must not wear a past life.
    """
    moment = now or datetime.now(UTC)
    grace = check_grace()
    born = {account.account_id: account.created_at for account in accounts}
    events = await list_all_lifecycle_events(list(born))
    own = [event for event in events if event.created_at >= born[event.account_id]]
    survivals: dict[str, AccountSurvival] = {}
    for stint in build_stints(own):
        facts = stint_facts(stint)
        if not stint.current or facts.released_at is None:
            continue
        survivals[stint.account_id] = AccountSurvival(
            days_since_release=max(0, (moment - facts.released_at).days),
            status=stint_status(facts, grace),
            cause=facts.cause,
            wounds=len(facts.wounds),
        )
    return survivals
