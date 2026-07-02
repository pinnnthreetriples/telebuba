"""Accounts read models — cursor-paginated page, listener filter, and fleet stats."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.db import (
    account_summary_counts,
    list_accounts,
    list_device_fingerprints,
    list_spam_statuses,
    list_warming_states,
)
from schemas.accounts import (
    AccountList,
    AccountStats,
    health_for_status,
)
from schemas.api import Page
from services.trust import account_trust_score_from

if TYPE_CHECKING:
    from schemas.accounts import AccountRead

# Design stat-tile buckets (mirror the SPA's accountDesignStatus). Everything not
# listed here falls into "problem" — the banned/errored catch-all.
_STATS_NEEDS_CODE = {"unauthorized", "new"}


class InvalidCursorError(ValueError):
    """A pagination cursor that cannot be decoded into an offset."""


# Cursor is an opaque offset token: the next page's start offset, as a string.
# The accounts source is offset-paginated, so the offset *is* the cursor; the
# client never parses it. ponytail: a real keyset cursor only if a list grows
# large enough that deep offsets hurt — accounts are small.
def _decode_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        offset = int(cursor)
    except ValueError as exc:
        raise InvalidCursorError(cursor) from exc
    if offset < 0:
        raise InvalidCursorError(cursor)
    return offset


async def list_accounts_page(
    *,
    query: str = "",
    status: str = "all",
    cursor: str | None = None,
    limit: int = 50,
) -> Page[AccountRead]:
    """One cursor-paginated page of accounts as the API ``Page[AccountRead]`` envelope.

    Fetches ``limit + 1`` rows to detect whether a further page exists without a
    second count query; the extra row is dropped and only signals ``next_cursor``.
    """
    offset = _decode_cursor(cursor)
    result = await list_accounts(query=query, status=status, limit=limit + 1, offset=offset)
    has_more = len(result.accounts) > limit
    items = result.accounts[:limit]
    await _attach_signals(items)
    next_cursor = str(offset + limit) if has_more else None
    return Page(items=items, next_cursor=next_cursor)


async def _attach_signals(accounts: list[AccountRead]) -> None:
    """Enrich a page of accounts in place with Trust Score + last spam verdict.

    Bulk-loads warming state, spam verdicts and device fingerprints once (mirrors
    the warming board's pattern) so the table is not an N+1; trust is then a pure
    per-account computation over the already-loaded signals.
    """
    if not accounts:
        return
    records = {record.account_id: record for record in await list_warming_states()}
    spam_by_account = await list_spam_statuses()
    fingerprints = await list_device_fingerprints()
    now = datetime.now(UTC)
    for account in accounts:
        spam = spam_by_account.get(account.account_id)
        fingerprint = fingerprints.get(account.account_id)
        trust = account_trust_score_from(
            account=account,
            record=records.get(account.account_id),
            spam=spam,
            lang_code=fingerprint.system_lang_code if fingerprint else None,
            now=now,
        )
        account.trust_score = trust.score
        account.trust_band = trust.band
        if spam is not None:
            account.spam_status = spam.status
            account.spam_detail = spam.detail
        if fingerprint is not None:
            account.device_lang = fingerprint.system_lang_code


async def list_listener_accounts() -> AccountList:
    """Accounts with a live session — the only valid neurocomment-listener candidates.

    The listener must log in to subscribe to channel posts, so an account without an
    authorized session (``unauthorized`` / ``session_error`` / never checked) can never
    act as one. ``health_for_status(...) == "ok"`` is exactly the ``alive`` set; filter
    on it rather than hard-coding a status string, so the rule stays in one place.
    """
    accounts = await list_accounts()
    return AccountList(
        accounts=[a for a in accounts.accounts if health_for_status(a.status) == "ok"],
    )


async def account_stats() -> AccountStats:
    """Fleet-wide status counts for the Accounts page tiles.

    Counts the whole table in one grouped SQL query (``account_summary_counts``),
    so the tiles are independent of which page the UI currently shows.
    """
    return _stats_from_counts(await account_summary_counts())


def _stats_from_counts(counts: dict[str, int]) -> AccountStats:
    return AccountStats(
        total=sum(counts.values()),
        active=counts.get("alive", 0),
        idle=counts.get("flood_wait", 0),
        needs_code=sum(counts.get(status, 0) for status in _STATS_NEEDS_CODE),
        problem=sum(
            count
            for status, count in counts.items()
            if status not in {"alive", "flood_wait"} and status not in _STATS_NEEDS_CODE
        ),
    )
