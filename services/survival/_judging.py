"""Lives → judged lives: the label and cohort fields each row needs. Pure."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from core.config import settings
from services.survival._tally import StintCohorts, deciding_wave, judge

if TYPE_CHECKING:
    from schemas.accounts import AccountRead
    from schemas.warming import WarmingStateRecord
    from services.survival._stints import Life, Stint
    from services.survival._tally import Judged

_SECONDS_PER_HOUR = 3600


def check_grace() -> timedelta:
    """How early a cached @SpamBot answer may count for a window's end: one cache life."""
    return timedelta(seconds=settings.warming.spam_status_ttl_hours * _SECONDS_PER_HOUR)


def _opening_extra(wave: Stint) -> dict[str, object]:
    first = wave.events[0]
    return first.extra if first.kind == "warming_started" else {}


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_days(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _live_country(account: AccountRead | None) -> str | None:
    if account is None:
        return None
    return (account.proxy_country_code or "").upper() or None


def life_cohorts(
    life: Life,
    account: AccountRead | None,
    record: WarmingStateRecord | None,
) -> StintCohorts:
    """Persona, length and proxy country of the deciding wave, else the live account's.

    The live warming row only speaks for the wave it still describes (same start), and
    the live account only for its current life.
    """
    wave = deciding_wave(life)
    extra = _opening_extra(wave)
    own = record if life.current and record and record.started_at == wave.start_key else None
    live = account if life.current else None
    return StintCohorts(
        persona=_optional_text(extra.get("persona")) or (own.activity_persona if own else None),
        target_days=_optional_days(extra.get("target_days")) or (own.target_days if own else None),
        proxy_country=_optional_text(extra.get("proxy_country")) or _live_country(live),
    )


def judge_lives(
    lives: list[Life],
    accounts: dict[str, AccountRead],
    records: dict[str, WarmingStateRecord],
) -> list[Judged]:
    """Every life with its facts, cohort fields and row label."""
    judged: list[Judged] = []
    for life in lives:
        account = accounts.get(life.account_id) if life.current else None
        label = (account.label or life.account_id) if account else life.account_id
        cohorts = life_cohorts(life, account, records.get(life.account_id))
        judged.append(judge(life, cohorts, label))
    return judged
