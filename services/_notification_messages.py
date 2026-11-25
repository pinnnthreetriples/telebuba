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

from services._notification_spam import SPAM_ONLY_EVENTS, SpamNotice, spam_kind

if TYPE_CHECKING:
    from schemas.accounts import AccountRead
    from schemas.logs import LogEntry
    from schemas.notifications import NotificationKind

# A cycle that hit PeerFlood parks the account in quarantine (``_transitions``).
_CYCLE_EVENT = "warming_cycle_completed"
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
    # A session audit (the background watch or the operator's) found a session that is not ours.
    "session_audit_foreign_found": "foreign_session",
    # The watch found that Telegram no longer lists the account's stored passkey.
    "session_watch_passkey_missing": "passkey_missing",
    # The shared @SpamBot budget ran past its daily cap (``services.spam_intel``).
    "spam_intel_budget_exceeded": "spam_budget",
    # A session check found the account's key gone (journalled once per death).
    "account_session_dead": "session_dead",
    # The session guard failed a step or stopped making passes.
    "session_guard_failed": "session_guard",
    "session_guard_stalled": "session_guard",
    # The safety nets themselves: the copies of the keys, of the database, the key vault.
    "session_vault_off": "session_vault_off",
    "session_backup_failed": "session_backup_failed",
    "passkey_vault_unhealthy": "passkey_vault",
    "db_maintenance_failed": "db_maintenance",
    "account_twofa_store_failed": "twofa_store_failed",
    # A session check or the start-up look found the account's .session file gone.
    "account_session_file_missing": "session_file_missing",
    # A re-key or a second-device sign-in was cut short; the repair settled it or could not.
    "session_rekey_recovered": "interrupted_key_flow",
    "session_rekey_interrupted": "interrupted_key_flow",
    "second_device_interrupted": "interrupted_key_flow",
    "session_leftover_authorization": "interrupted_key_flow",
    "flow_intent_unresolved": "interrupted_key_flow",
    # The phone's Telegram app lost its sign-in; warming does not sign it in again by itself.
    "phone_signed_out": "phone_signed_out",
    "phone_authorization_orphaned": "phone_signed_out",
    # The watch saw the account's security settings move without Telebuba moving them.
    "twofa_reset_pending": "twofa_reset_pending",
    "security_settings_changed": "security_changed",
    # Telegram sent a login code nobody here asked for.
    "login_code_unrequested": "login_code_unrequested",
    # The background proxy check: a dead proxy, or one that now exits in another country.
    "proxy_check_failed": "proxy_problem",
    # The copies that leave the disk, and the key vault's own snapshot.
    "db_mirror_failed": "db_maintenance",
    "passkey_vault_snapshot_failed": "passkey_vault",
    # A folder with keys, the database or their copies is open to other users of this computer.
    "private_path_open": "private_path_open",
}
# ``proxy_exit_changed`` is news only when the country moved: a rotating proxy's IP moves all day.
_PROXY_EXIT_EVENT = "proxy_exit_changed"
# Kinds that tell one fleet-wide thing however many accounts raise it (a full disk fails every
# account's copy at once): the throttle slot is shared, not one per account.
FLEET_KINDS: frozenset[NotificationKind] = frozenset(
    {"session_backup_failed", "passkey_vault", "proxy_problem", "private_path_open"}
)

_PREFIX = "Telebuba · "
_TEMPLATES: dict[NotificationKind, str] = {
    "quarantine": "{label}: спам-блок (PeerFlood), аккаунт на карантине прогрева.",
    "fleet_dm_paused": "Прогрев: личные сообщения между аккаунтами приостановлены для всего парка.",
    # ``{until}`` is "до 29.10.2026" or "бессрочно"; ``{source}`` is " Источник: ..." or empty. The
    # last sentence is the appeal hint (design 5.12): a fixed text, the operator appeals by hand.
    "spam_limited": (
        "{label}: аккаунт ограничен за спам — {until}.{source} "
        "Апелляцию подаёте вы сами: на карточке аккаунта кнопка «Подать апелляцию»."
    ),
    "account_frozen": (
        "{label}: Telegram заморозил аккаунт — сообщения и вступления с него недоступны, "
        "пока заморозка не снята. Проверьте аккаунт в приложении Telegram."
    ),
    "spam_blocked": (
        "{label}: @SpamBot подтвердил спамблок — прогрев остановлен. "
        "Решите на карточке аккаунта: удалить или оставить."
    ),
    "error": "{label}: прогрев остановлен с ошибкой — нужна проверка.",
    "warmed": "{label}: прогрев завершён, аккаунт готов к передаче.",
    "foreign_session": (
        "{label}: на аккаунте появилась чужая сессия. "
        "Откройте карточку аккаунта, раздел «Сессии»: кто-то ещё держит этот аккаунт."
    ),
    "passkey_missing": (
        "{label}: ключ доступа аккаунта пропал из Telegram — "
        "в настройках безопасности аккаунта был кто-то ещё."
    ),
    "spam_budget": (
        "Опросы @SpamBot: превышен суточный лимит по парку, проверки откладываются. "
        "Откройте журнал событий (spam_intel_*): частые опросы выглядят как автоматизация."
    ),
    "session_dead": (
        "{label}: сессия аккаунта больше не работает. "
        "Войдите заново: ключом доступа (passkey) или кодом входа."
    ),
    "session_guard": (
        "Сторож сессий дал сбой или перестал делать проходы — "
        "проверка сессий и закрытие старых ключей могут не работать."
    ),
    "session_vault_off": (
        "Копии ключей аккаунтов не делаются: не задан SESSION_VAULT__KEY. "
        "Потеряете файл сессии — аккаунт не вернуть. Инструкция: docs/session-vault.md."
    ),
    "session_backup_failed": (
        "Не удалось сохранить копию ключа аккаунта (сессии) в хранилище или зеркало. "
        "Проверьте место на диске и доступность зеркала; попытка повторится сама."
    ),
    "passkey_vault": (
        "Сейф ключей доступа неисправен или недоступен: вход по ключу доступа (passkey) "
        "может не работать. Подробности в журнале событий (passkey_vault_unhealthy)."
    ),
    "db_maintenance": (
        "Не удалось сделать копию или обслужить базу данных (в ней пароли 2FA и привязки прокси). "
        "Проверьте место на диске; попытка повторится сама."
    ),
    "twofa_store_failed": (
        "{label}: облачный пароль (2FA) не сохранён в базе. "
        "Откройте карточку аккаунта и задайте пароль заново, иначе повторный вход не сработает."
    ),
    "session_file_missing": (
        "{label}: файл сессии пропал из папки sessions (антивирус, перенос), "
        "аккаунт не работает. Ключ в Telegram может быть ещё жив — верните файл из копии "
        "(docs/session-vault.md) или войдите по коду."
    ),
    "interrupted_key_flow": (
        "{label}: смена ключа или вход второго устройства были прерваны. "
        "Откройте карточку аккаунта, раздел «Сессии»: проверьте, нет ли лишней сессии "
        "и закрыт ли старый ключ."
    ),
    "phone_signed_out": (
        "{label}: приложение Telegram на телефоне (эмулятор) вышло из аккаунта. "
        "Прогрев не входит заново сам — откройте телефон аккаунта вручную. "
        "Если прежний вход ещё виден в «Сессиях», закройте его."
    ),
    "twofa_reset_pending": (
        "{label}: запрошен СБРОС облачного пароля — через 7 дней пароль будет снят. "
        "Если это не вы, откройте карточку аккаунта и отмените сброс."
    ),
    "security_changed": (
        "{label}: облачный пароль или почта восстановления изменились не из Telebuba. "
        "Проверьте карточку аккаунта: в настройках безопасности был кто-то ещё."
    ),
    "login_code_unrequested": (
        "{label}: пришёл код входа, который Telebuba не запрашивала, — кто-то пытается войти "
        "в аккаунт. Проверьте раздел «Сессии» на карточке аккаунта."
    ),
    "private_path_open": (
        "Папка с ключами аккаунтов, базой или их копиями открыта другим пользователям "
        "этого компьютера. Посмотрите: python -m tools.secure_paths check; "
        "закрыть: python -m tools.secure_paths fix (docs/session-vault.md)."
    ),
    "proxy_problem": (
        "Фоновая проверка прокси: прокси перестал отвечать или сменил страну выхода. "
        "Аккаунты на нём под риском — откройте раздел «Прокси»."
    ),
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
    # Only the news: an account already limited (a kept spam block is permanent) answers
    # "limited" at every scheduled check and must not ping each time; a freeze is told once,
    # whichever of the session check and the fold finds it first (they share one throttle slot).
    spam = spam_kind(entry)
    if spam is not None:
        return spam
    if entry.event in SPAM_ONLY_EVENTS:
        return None
    if entry.event == _PROXY_EXIT_EVENT:
        return "proxy_problem" if entry.extra.get("what") == "country" else None
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


def compose_message(kind: NotificationKind, label: str, notice: SpamNotice | None = None) -> str:
    """The full message text for one notification (``notice``: what a spam message names)."""
    until = "бессрочно" if notice is None or notice.until is None else f"до {notice.until:%d.%m.%Y}"
    source = f" Источник: {notice.source}." if notice is not None and notice.source else ""
    return _PREFIX + _TEMPLATES[kind].format(label=label, until=until, source=source)
