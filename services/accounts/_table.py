"""Accounts-table rendering — filter, summarise, and format rows for the UI."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.db import account_summary_counts, list_accounts
from schemas.accounts import (
    AccountList,
    AccountsTableState,
    AccountSummary,
    AccountTableRow,
    health_for_status,
)

if TYPE_CHECKING:
    from schemas.accounts import AccountFilter, AccountRead

_PERMANENT_ISSUES = {"unauthorized", "session_error", "account_error"}
_TEMPORARY_ISSUES = {"flood_wait", "network_error", "proxy_error", "unknown_error"}
_SECONDS_PER_MINUTE = 60
_SECONDS_PER_HOUR = 3600
_SECONDS_PER_DAY = 86_400


async def load_accounts_table(data: AccountFilter) -> AccountsTableState:
    # DB-level filter + optional pagination so the UI does not have to pull
    # the entire accounts table into memory just to render one page.
    accounts = await list_accounts(
        query=data.query,
        status=data.status if data.status != "all" else "all",
        limit=data.limit,
        offset=data.offset,
    )
    summary = _summary_from_counts(await account_summary_counts())
    return AccountsTableState(
        rows=[_to_table_row(account) for account in accounts.accounts],
        summary=summary,
    )


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


def _summary_from_counts(counts: dict[str, int]) -> AccountSummary:
    return AccountSummary(
        total=sum(counts.values()),
        alive=counts.get("alive", 0),
        permanent_issue=sum(counts.get(status, 0) for status in _PERMANENT_ISSUES),
        temporary_issue=sum(counts.get(status, 0) for status in _TEMPORARY_ISSUES),
        never_checked=counts.get("new", 0),
    )


def _to_table_row(account: AccountRead) -> AccountTableRow:
    return AccountTableRow(
        account_id=account.account_id,
        label=account.label or account.account_id,
        # Raw status enum — translated to RU once in the UI layer
        # (features.accounts._table._account_status_label), the same map the
        # check-result toast uses. Emitting an English label here forced a
        # second translation that silently fell through for three statuses.
        status=account.status,
        health=health_for_status(account.status),
        telegram=_telegram_label(account),
        session=account.session_name or account.account_id,
        device=_device_label(account),
        proxy=_proxy_label(account),
        last_checked=_format_last_checked(account.last_checked_at),
        first_name=account.first_name,
        last_name=account.last_name,
        username=account.username,
        bio=account.bio,
        proxy_type=account.proxy_type,
        proxy_host=account.proxy_host,
        proxy_port=account.proxy_port,
        proxy_status=account.proxy_status,
        proxy_last_checked_at=account.proxy_last_checked_at,
        proxy_last_error=account.proxy_last_error,
        proxy_exit_ip=account.proxy_exit_ip,
        proxy_country_code=account.proxy_country_code,
        proxy_country_name=account.proxy_country_name,
    )


def _format_last_checked(iso_value: str | None, now: datetime | None = None) -> str:
    """Render an ISO-8601 timestamp as a compact relative RU string for the UI.

    Returns the ``"never"`` sentinel when ``iso_value`` is empty (the UI maps
    it to «никогда»). Otherwise produces «N сек/мин/ч/дн назад». Falls back to
    the raw string if parsing fails — we never want a single bad row to break
    the whole table. Abbreviated units sidestep RU plural agreement.
    """
    if not iso_value:
        return "never"
    try:
        moment = datetime.fromisoformat(iso_value)
    except ValueError:
        return iso_value
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    reference = now or datetime.now(UTC)
    seconds = max(0, int((reference - moment).total_seconds()))
    if seconds < _SECONDS_PER_MINUTE:
        return f"{seconds} сек назад"
    if seconds < _SECONDS_PER_HOUR:
        return f"{seconds // _SECONDS_PER_MINUTE} мин назад"
    if seconds < _SECONDS_PER_DAY:
        return f"{seconds // _SECONDS_PER_HOUR} ч назад"
    return f"{seconds // _SECONDS_PER_DAY} дн назад"


def _telegram_label(account: AccountRead) -> str:
    name = " ".join(part for part in (account.first_name, account.last_name) if part)
    username = f"@{account.username}" if account.username else ""
    phone = account.phone or ""
    return " | ".join(part for part in (name, username, phone) if part) or "-"


def _device_label(account: AccountRead) -> str:
    return (
        " | ".join(
            part
            for part in (
                account.device_model,
                account.device_system_version,
                account.device_app_version,
            )
            if part
        )
        or "-"
    )


def _proxy_label(account: AccountRead) -> str:
    if not account.proxy_type or not account.proxy_host or account.proxy_port is None:
        return "-"
    base = f"{account.proxy_type.upper()} {account.proxy_host}:{account.proxy_port}"
    code = account.proxy_country_code
    return f"{base} · {code}" if code else base
