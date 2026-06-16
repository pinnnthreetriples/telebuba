"""Accounts-table rendering — filter, summarise, and format rows for the UI."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.db import list_accounts
from schemas.accounts import (
    AccountsTableState,
    AccountSummary,
    AccountTableRow,
    health_for_status,
)

if TYPE_CHECKING:
    from schemas.accounts import AccountFilter, AccountRead, AccountStatus

_PERMANENT_ISSUES = {"unauthorized", "session_error", "account_error"}
_TEMPORARY_ISSUES = {"flood_wait", "network_error", "proxy_error", "unknown_error"}
_SECONDS_PER_MINUTE = 60
_SECONDS_PER_HOUR = 3600
_SECONDS_PER_DAY = 86_400


async def load_accounts_table(data: AccountFilter) -> AccountsTableState:
    accounts = await list_accounts()
    filtered = [account for account in accounts.accounts if _matches_filter(account, data)]
    return AccountsTableState(
        rows=[_to_table_row(account) for account in filtered],
        summary=_summarize(accounts.accounts),
    )


def _matches_filter(account: AccountRead, data: AccountFilter) -> bool:
    if data.status not in ("all", account.status):
        return False
    if not data.query:
        return True
    haystack = " ".join(
        value or ""
        for value in (
            account.account_id,
            account.label,
            account.phone,
            account.username,
            account.first_name,
            account.last_name,
            account.session_name,
        )
    ).lower()
    return data.query.lower() in haystack


def _summarize(accounts: list[AccountRead]) -> AccountSummary:
    return AccountSummary(
        total=len(accounts),
        alive=sum(account.status == "alive" for account in accounts),
        permanent_issue=sum(account.status in _PERMANENT_ISSUES for account in accounts),
        temporary_issue=sum(account.status in _TEMPORARY_ISSUES for account in accounts),
        never_checked=sum(account.status == "new" for account in accounts),
    )


def _to_table_row(account: AccountRead) -> AccountTableRow:
    return AccountTableRow(
        account_id=account.account_id,
        label=account.label or account.account_id,
        status=_status_label(account.status),
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
    """Render an ISO-8601 timestamp as a compact relative string for the UI.

    Returns "never" when ``iso_value`` is empty. Otherwise produces
    ``Ns ago`` / ``Nm ago`` / ``Nh ago`` / ``Nd ago``. Falls back to the
    raw string if parsing fails — we never want a single bad row to break
    the whole table.
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
        return f"{seconds}s ago"
    if seconds < _SECONDS_PER_HOUR:
        return f"{seconds // _SECONDS_PER_MINUTE}m ago"
    if seconds < _SECONDS_PER_DAY:
        return f"{seconds // _SECONDS_PER_HOUR}h ago"
    return f"{seconds // _SECONDS_PER_DAY}d ago"


def _status_label(status: AccountStatus) -> str:
    labels = {
        "new": "New",
        "alive": "Alive",
        "unauthorized": "Unauthorized",
        "session_error": "Session error",
        "account_error": "Account error",
        "flood_wait": "Flood wait",
        "network_error": "Network",
        "proxy_error": "Proxy",
        "unknown_error": "Unknown",
    }
    return labels[status]


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
    return f"{account.proxy_type.upper()} {account.proxy_host}:{account.proxy_port}"
