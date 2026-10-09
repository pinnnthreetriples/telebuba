"""Accounts read models — cursor-paginated page, listener filter, and fleet stats."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, get_args

from core.db import (
    account_summary_counts,
    list_accounts,
    list_device_fingerprints,
    list_spam_statuses,
    list_warming_states,
)
from core.phone_geo import calling_code_for_phone
from core.repositories.account_folders import (
    count_filtered_accounts,
    folder_ids_by_account,
    list_filtered_accounts,
)
from schemas.account_folders import NO_PROXY, AccountListFilters
from schemas.accounts import (
    AccountList,
    AccountPage,
    AccountStats,
    AccountStatus,
    health_for_status,
)
from services.trust import account_trust_score_from

if TYPE_CHECKING:
    from schemas.accounts import AccountRead

# Design stat-tile buckets (mirror the SPA's accountDesignStatus). Everything not
# listed here falls into "problem" — the banned/frozen/errored catch-all.
_STATS_NEEDS_CODE = {"unauthorized", "new"}
# The same buckets as the list's ``status`` filter values.
_STATUS_GROUPS: dict[str, frozenset[str]] = {
    "active": frozenset({"alive"}),
    "idle": frozenset({"flood_wait"}),
    "needs_code": frozenset(_STATS_NEEDS_CODE),
    "problem": frozenset(get_args(AccountStatus)) - {"alive", "flood_wait"} - _STATS_NEEDS_CODE,
}


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


async def list_accounts_page(  # noqa: PLR0913 - one keyword per filter of the page
    *,
    query: str = "",
    status: str = "all",
    folder: str | None = None,
    phone_code: int | None = None,
    proxy_country: str | None = None,
    min_trust: int | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> AccountPage:
    """One cursor-paginated page of accounts, with how many match the filters in all.

    ``status`` is ``all``, a stat-tile bucket (``active``/``idle``/``needs_code``/
    ``problem``) or one raw status. Folder, status and proxy country filter in SQL and
    page there; the phone calling code and Trust exist only in Python (``phonenumbers``,
    the Trust Score), so either one reads every SQL match and pages the survivors.
    """
    offset = _decode_cursor(cursor)
    filters = AccountListFilters(
        query=query,
        statuses=_statuses_for(status),
        folder=folder,
        proxy_country=None if proxy_country is None else _proxy_country(proxy_country),
    )
    if phone_code is None and min_trust is None:
        result = await list_filtered_accounts(filters, limit=limit, offset=offset)
        items = result.accounts
        total = await count_filtered_accounts(filters)
        await _attach_signals(items)
    else:
        matches = (await list_filtered_accounts(filters)).accounts
        if phone_code is not None:
            matches = [a for a in matches if calling_code_for_phone(a.phone) == phone_code]
        await _attach_signals(matches)
        if min_trust is not None:
            matches = [a for a in matches if (a.trust_score or 0) >= min_trust]
        total = len(matches)
        items = matches[offset : offset + limit]
    await _attach_folders(items)
    next_cursor = str(offset + limit) if offset + limit < total else None
    return AccountPage(items=items, next_cursor=next_cursor, total=total)


def _proxy_country(value: str) -> str:
    return value if value == NO_PROXY else value.upper()


def _statuses_for(status: str) -> frozenset[str] | None:
    if status == "all":
        return None
    return _STATUS_GROUPS.get(status, frozenset({status}))


async def _attach_folders(accounts: list[AccountRead]) -> None:
    if not accounts:
        return
    folders = await folder_ids_by_account()
    for account in accounts:
        account.folder_ids = folders.get(account.account_id, [])


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
