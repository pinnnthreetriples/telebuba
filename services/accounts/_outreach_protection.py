"""Account protection shared by the contact lookup and bulk-message runs.

Which accounts are young enough for half caps, which a run leaves out before it
starts (resting after a limit, spam-blocked, flooded in the last 24 hours), and the
rest an account takes after it meets a limit. Reuses what the app already stores:
the operator's registration date or the fleet join time, the cached @SpamBot
verdicts and warming's flood memory, plus outreach's own rests.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.repositories.accounts import list_accounts_by_ids
from core.repositories.outreach_rests import list_outreach_rests, record_outreach_rest
from core.repositories.spam_status import list_spam_statuses_by_ids
from core.repositories.warming_floods import list_warming_flood_summaries
from schemas.outreach_protection import OutreachRest, SkippedAccount

if TYPE_CHECKING:
    from collections.abc import Sequence

    from schemas.outreach_protection import OutreachProtection

# Error codes that mean "this account hit a Telegram limit": it rests, and enough of
# them stop the run. Slow mode is a chat's setting, not the account's, so it is not one.
LIMIT_CODES = frozenset({"flood_wait", "peer_flood", "premium_wait"})
# How far back a flood still counts as fresh enough to leave the account out.
_INJURY_WINDOW = timedelta(hours=24)


def halve(cap: int) -> int:
    """A young account's cap: half, rounded down, never below one."""
    return max(cap // 2, 1)


def _moment(value: str) -> datetime:
    moment = datetime.fromisoformat(value)
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


async def young_account_ids(
    account_ids: Sequence[str], protection: OutreachProtection, now: datetime
) -> set[str]:
    """Accounts younger than the threshold; none while half caps are off.

    Age is the Telegram registration date when the operator gave one, else the time
    since the account joined the fleet. An account missing from the fleet is not young.
    """
    if not protection.young_half_caps or not account_ids:
        return set()
    accounts = (await list_accounts_by_ids(list(account_ids))).accounts
    young = timedelta(days=protection.young_account_days)
    return {
        account.account_id
        for account in accounts
        if now - _moment(account.telegram_registered_at or account.created_at) < young
    }


async def skipped_accounts(
    account_ids: Sequence[str], protection: OutreachProtection, now: datetime
) -> list[SkippedAccount]:
    """Accounts a run starting ``now`` leaves out, in ``account_ids`` order, with why."""
    if not account_ids or not (protection.rest_after_limit or protection.skip_injured):
        return []
    rests = await list_outreach_rests(account_ids)
    spam = await list_spam_statuses_by_ids(list(account_ids)) if protection.skip_injured else {}
    floods = await list_warming_flood_summaries() if protection.skip_injured else {}
    since = now - _INJURY_WINDOW
    skipped: list[SkippedAccount] = []
    for account_id in account_ids:
        rest = rests.get(account_id)
        if protection.rest_after_limit and rest is not None and _moment(rest.rest_until) > now:
            left = int((_moment(rest.rest_until) - now).total_seconds())
            skipped.append(
                SkippedAccount(
                    account_id=account_id, reason="account_resting", retry_after_seconds=left
                )
            )
            continue
        if not protection.skip_injured:
            continue
        verdict = spam.get(account_id)
        if verdict is not None and verdict.status == "limited":
            skipped.append(SkippedAccount(account_id=account_id, reason="account_spam_blocked"))
            continue
        flood = floods.get(account_id)
        flooded_at = [
            _moment(value)
            for value in (
                flood.last_flood_at if flood is not None else None,
                rest.limited_at if rest is not None else None,
            )
            if value
        ]
        if any(moment > since for moment in flooded_at):
            skipped.append(SkippedAccount(account_id=account_id, reason="account_recent_flood"))
    return skipped


async def record_limit(
    account_id: str,
    code: str,
    retry_after_seconds: int | None,
    protection: OutreachProtection,
    now: datetime,
) -> None:
    """Remember a limit; with rests on, the account rests the longer of X hours and the wait.

    Stored even with rests off, so "a flood in the last 24 hours" sees outreach floods too.
    """
    rest_until = now
    if protection.rest_after_limit:
        wait = timedelta(seconds=retry_after_seconds or 0)
        rest_until = now + max(timedelta(hours=protection.rest_hours), wait)
    await record_outreach_rest(
        OutreachRest(
            account_id=account_id,
            reason=code,
            limited_at=now.isoformat(),
            rest_until=rest_until.isoformat(),
        )
    )


async def note_limit(
    account_id: str,
    code: str,
    retry_after_seconds: int | None,
    protection: OutreachProtection,
    limited_accounts: int,
) -> bool:
    """Record a limit now; True when ``limited_accounts`` (this one included) stop the run."""
    await record_limit(account_id, code, retry_after_seconds, protection, datetime.now(UTC))
    return protection.stop_run_on_limits and limited_accounts >= protection.stop_run_after_accounts
