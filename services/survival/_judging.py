"""Stints → judged stints: the label and cohort fields each row needs. Pure."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from core.config import settings
from services.survival._tally import StintCohorts, judge

if TYPE_CHECKING:
    from schemas.accounts import AccountRead
    from schemas.warming import WarmingStateRecord
    from services.survival._stints import Stint
    from services.survival._tally import Judged

_SECONDS_PER_HOUR = 3600


def check_grace() -> timedelta:
    """How early a cached @SpamBot answer may count for a window's end: one cache life."""
    return timedelta(seconds=settings.warming.spam_status_ttl_hours * _SECONDS_PER_HOUR)


def _opening_extra(stint: Stint) -> dict[str, object]:
    first = stint.events[0]
    return first.extra if first.kind == "warming_started" else {}


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_days(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _live_country(account: AccountRead | None) -> str | None:
    if account is None:
        return None
    return (account.proxy_country_code or "").upper() or None


def stint_cohorts(
    stint: Stint,
    account: AccountRead | None,
    record: WarmingStateRecord | None,
) -> StintCohorts:
    """Persona, length and proxy country from the start row, else the live account's.

    The live warming row only speaks for the stint it still describes (same start), and
    the live account only for its current life.
    """
    extra = _opening_extra(stint)
    own = record if stint.current and record and record.started_at == stint.start_key else None
    live = account if stint.current else None
    return StintCohorts(
        persona=_optional_text(extra.get("persona")) or (own.activity_persona if own else None),
        target_days=_optional_days(extra.get("target_days")) or (own.target_days if own else None),
        proxy_country=_optional_text(extra.get("proxy_country")) or _live_country(live),
    )


def judge_stints(
    stints: list[Stint],
    accounts: dict[str, AccountRead],
    records: dict[str, WarmingStateRecord],
) -> list[Judged]:
    """Every stint with its facts, cohort fields and row label."""
    judged: list[Judged] = []
    for stint in stints:
        account = accounts.get(stint.account_id) if stint.current else None
        label = (account.label or stint.account_id) if account else stint.account_id
        cohorts = stint_cohorts(stint, account, records.get(stint.account_id))
        judged.append(judge(stint, cohorts, label))
    return judged
