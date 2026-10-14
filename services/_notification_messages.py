"""Which log events reach the operator bot, and what the message says.

Every template lives here. These are outbound bot messages, not API responses, so they
are written in the operator's language (Russian) rather than handed to the SPA as codes.
A message names an account by its Telegram name or username, never by phone number,
and never quotes a log row's ``extra`` — that is where error text could carry secrets.
"""

# ruff: noqa: RUF001 - the templates are Russian text on purpose; Cyrillic is not a typo.

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from schemas.accounts import AccountRead
    from schemas.logs import LogEntry
    from schemas.notifications import NotificationKind

# A cycle that hit PeerFlood parks the account in quarantine (``_transitions``).
_CYCLE_EVENT = "warming_cycle_completed"
_SPAM_STATUS_EVENT = "spam_status_refreshed"
# The fixed-name events; ``warming_dm_fleet_paused`` is fleet-wide (no account).
_EVENT_KINDS: dict[str, NotificationKind] = {
    "warming_quarantine_extended": "quarantine",
    "warming_dm_fleet_paused": "fleet_dm_paused",
    # The quarantine re-check confirmed the block: warming stopped, the card asks to decide.
    "warming_spam_blocked": "spam_blocked",
    # Each of these leaves the account in the warming ``error`` column.
    "warming_quarantine_unreadable": "error",
    "warming_loop_crashed": "error",
    "warming_target_reached": "warmed",
}

_PREFIX = "Telebuba · "
_TEMPLATES: dict[NotificationKind, str] = {
    "quarantine": "{label}: спам-блок (PeerFlood), аккаунт на карантине прогрева.",
    "fleet_dm_paused": "Прогрев: личные сообщения между аккаунтами приостановлены для всего парка.",
    "spam_limited": "{label}: @SpamBot сообщает об ограничениях аккаунта.",
    "spam_blocked": (
        "{label}: @SpamBot подтвердил спамблок — прогрев остановлен. "
        "Решите на карточке аккаунта: удалить или оставить."
    ),
    "error": "{label}: прогрев остановлен с ошибкой — нужна проверка.",
    "warmed": "{label}: прогрев завершён, аккаунт готов к передаче.",
}
TEST_MESSAGE = f"{_PREFIX}тестовое уведомление: бот настроен и пишет в этот чат."
UNNAMED_ACCOUNT = "аккаунт без имени"

# Seven or more digits, optionally split by spaces, dashes or brackets: a phone number
# an operator may have typed as a label. Such a candidate is skipped, never sent.
_PHONE_LIKE = re.compile(r"\+?\d[\d\s()-]{5,}\d")


def notification_kind(entry: LogEntry) -> NotificationKind | None:
    """The notification a log row asks for, or ``None`` for every other row."""
    if entry.event == _CYCLE_EVENT:
        return "quarantine" if entry.extra.get("status") == "peer_flood" else None
    if entry.event == _SPAM_STATUS_EVENT:
        # Only the news: an account already limited (a kept spam block is permanent)
        # answers "limited" at every scheduled check and must not ping each time.
        news = entry.extra.get("previous") != "limited"
        return "spam_limited" if entry.extra.get("status") == "limited" and news else None
    return _EVENT_KINDS.get(entry.event)


def _usable(candidate: str | None) -> str | None:
    text = (candidate or "").strip()
    return text if text and not _PHONE_LIKE.search(text) else None


def account_label(account: AccountRead | None) -> str:
    """Telegram name and @username, else the operator's label, else a neutral word."""
    if account is None:
        return UNNAMED_ACCOUNT
    name = _usable(" ".join(part for part in (account.first_name, account.last_name) if part))
    handle = f"@{account.username}" if account.username else None
    if name and handle:
        return f"{name} ({handle})"
    return name or handle or _usable(account.label) or UNNAMED_ACCOUNT


def compose_message(kind: NotificationKind, label: str) -> str:
    """The full message text for one notification."""
    return _PREFIX + _TEMPLATES[kind].format(label=label)
