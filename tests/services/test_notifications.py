"""The operator-bot runtime: event mapping, the per-(account, kind) throttle, failure safety."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core import events
from core.config import settings
from core.db import list_recent_logs
from core.logging import log_event, reset_logging_for_tests, setup_logging
from core.repositories.notification_settings import save_notification_settings
from schemas.accounts import AccountRead
from schemas.logs import LogEntry
from schemas.notifications import BotMessage, BotSendResult
from services import notifications
from services._notification_messages import account_label, notification_kind

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_TOKEN = "123456:AAH-secret_token_value_abcdefXYZ"
_HOUR = 3600.0


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    reset_logging_for_tests()
    setup_logging()
    notifications.reset_for_tests()
    yield
    notifications.reset_for_tests()
    reset_logging_for_tests()


class _Bot:
    """Records every message; answers with ``result``."""

    def __init__(self, result: BotSendResult | None = None) -> None:
        self.sent: list[BotMessage] = []
        self.result = result or BotSendResult(status="sent")

    async def __call__(self, message: BotMessage) -> BotSendResult:
        self.sent.append(message)
        return self.result


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def bot(monkeypatch: pytest.MonkeyPatch) -> _Bot:
    stub = _Bot()
    monkeypatch.setattr(notifications, "send_bot_message", stub)
    return stub


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    stub = _Clock()
    monkeypatch.setattr(notifications._runtime, "clock", stub)
    return stub


@pytest.fixture
def named_account(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fetch(account_id: str) -> AccountRead:
        return AccountRead(
            account_id=account_id,
            status="alive",
            phone="+79991234567",
            first_name="Анна",
            username="anna_w",
            created_at="now",
            updated_at="now",
        )

    monkeypatch.setattr(notifications, "fetch_account", _fetch)


async def _configure(*, enabled: bool = True) -> None:
    await save_notification_settings(enabled=enabled, bot_token=_TOKEN, chat_id="-100500")


def _entry(event: str, account_id: str | None = "acc-1", **extra: object) -> LogEntry:
    return LogEntry(
        id=1,
        created_at="now",
        level="WARNING",
        status="warning",
        account_id=account_id,
        event=event,
        extra=dict(extra),
    )


@pytest.mark.parametrize(
    ("entry", "kind"),
    [
        (_entry("warming_cycle_completed", status="peer_flood"), "quarantine"),
        (_entry("warming_cycle_completed", status="flood_wait"), None),
        (_entry("warming_cycle_completed", status="ok"), None),
        (_entry("warming_quarantine_extended"), "quarantine"),
        (_entry("warming_dm_fleet_paused", account_id=None), "fleet_dm_paused"),
        (_entry("spam_status_refreshed", status="limited"), "spam_limited"),
        (_entry("spam_status_refreshed", status="clean"), None),
        (_entry("spam_status_refreshed", status="unknown"), None),
        (_entry("warming_quarantine_exhausted"), "error"),
        (_entry("warming_quarantine_unreadable"), "error"),
        (_entry("warming_loop_crashed"), "error"),
        (_entry("warming_target_reached"), "warmed"),
        (_entry("warming_started"), None),
        (_entry("notifications_send_failed"), None),
    ],
)
def test_events_map_to_notification_kinds(entry: LogEntry, kind: str | None) -> None:
    assert notification_kind(entry) == kind


def test_the_label_never_carries_a_phone_number() -> None:
    def _account(**fields: str) -> AccountRead:
        base = {
            "account_id": "79991234567",
            "status": "alive",
            "created_at": "n",
            "updated_at": "n",
        }
        return AccountRead.model_validate({**base, **fields})

    phone = "+7 999 123-45-67"
    assert account_label(_account(phone=phone, label=phone)) == "аккаунт без имени"
    assert account_label(_account(label="Склад 3")) == "Склад 3"
    assert account_label(_account(username="ops")) == "@ops"
    assert account_label(_account(first_name="Иван", label="Склад 3")) == "Иван"
    assert account_label(None) == "аккаунт без имени"


@pytest.mark.usefixtures("named_account")
@pytest.mark.asyncio
async def test_a_mapped_event_sends_one_message_naming_the_account(bot: _Bot) -> None:
    await _configure()
    await notifications.handle_log_entry(_entry("warming_target_reached"))
    assert len(bot.sent) == 1
    message = bot.sent[0]
    assert (message.bot_token, message.chat_id) == (_TOKEN, "-100500")
    assert "Анна (@anna_w)" in message.text
    assert "готов к передаче" in message.text
    assert "9991234567" not in message.text


@pytest.mark.usefixtures("named_account")
@pytest.mark.asyncio
async def test_disabled_or_unconfigured_sends_nothing(bot: _Bot) -> None:
    await notifications.handle_log_entry(_entry("warming_target_reached"))
    await _configure(enabled=False)
    await notifications.handle_log_entry(_entry("warming_target_reached"))
    await save_notification_settings(enabled=True, bot_token=_TOKEN, chat_id="")
    await notifications.handle_log_entry(_entry("warming_target_reached"))
    assert bot.sent == []


@pytest.mark.usefixtures("named_account")
@pytest.mark.asyncio
async def test_one_message_per_account_and_kind_every_six_hours(bot: _Bot, clock: _Clock) -> None:
    await _configure()
    flood = _entry("warming_cycle_completed", status="peer_flood")
    await notifications.handle_log_entry(flood)
    await notifications.handle_log_entry(_entry("warming_quarantine_extended"))  # same kind
    await notifications.handle_log_entry(_entry("warming_target_reached"))  # other kind
    await notifications.handle_log_entry(
        _entry("warming_cycle_completed", account_id="acc-2", status="peer_flood"),
    )
    assert len(bot.sent) == 3

    clock.now += 6 * _HOUR - 1
    await notifications.handle_log_entry(flood)
    assert len(bot.sent) == 3
    clock.now += 1
    await notifications.handle_log_entry(flood)
    assert len(bot.sent) == 4


@pytest.mark.asyncio
async def test_the_throttle_window_comes_from_config(
    bot: _Bot,
    clock: _Clock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.notifications, "throttle_hours", 1.0)
    await _configure()
    fleet = _entry("warming_dm_fleet_paused", account_id=None)
    await notifications.handle_log_entry(fleet)
    clock.now += _HOUR
    await notifications.handle_log_entry(fleet)
    assert len(bot.sent) == 2
    assert "всего парка" in bot.sent[0].text


@pytest.mark.usefixtures("named_account")
@pytest.mark.asyncio
async def test_a_failed_send_is_logged_without_the_token_and_swallowed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(
        notifications,
        "send_bot_message",
        _Bot(BotSendResult(status="unreachable", error_type="ConnectError")),
    )
    await _configure()
    with caplog.at_level("DEBUG"):
        await notifications.handle_log_entry(_entry("warming_loop_crashed"))

    rows = [row for row in await list_recent_logs() if row.event == "notifications_send_failed"]
    assert len(rows) == 1
    assert rows[0].extra == {"kind": "error", "status": "unreachable", "error_type": "ConnectError"}
    reset_logging_for_tests()  # flush the file sink
    everything = caplog.text + (tmp_path / "debug.log").read_text(encoding="utf-8")
    everything += " ".join(row.model_dump_json() for row in await list_recent_logs())
    assert _TOKEN not in everything
    assert _TOKEN.split(":")[1] not in everything


@pytest.mark.asyncio
async def test_the_runtime_follows_the_bus_and_survives_a_failing_row(
    bot: _Bot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _configure()
    real = notifications.handle_log_entry
    seen: list[str] = []

    async def _flaky(entry: LogEntry) -> None:
        seen.append(entry.event)
        if entry.event == "warming_started":
            message = "boom"
            raise RuntimeError(message)
        await real(entry)

    monkeypatch.setattr(notifications, "handle_log_entry", _flaky)
    before = events.subscriber_count()
    await notifications.start_notifications()
    await notifications.start_notifications()  # a second start is a no-op
    assert events.subscriber_count() == before + 1
    try:
        await log_event("INFO", "warming_started", account_id="acc-1")
        await log_event("INFO", "warming_target_reached", account_id="acc-1")
        for _ in range(100):
            if bot.sent:
                break
            await asyncio.sleep(0.01)
    finally:
        await notifications.shutdown_notifications()
    assert seen[:2] == ["warming_started", "warming_target_reached"]
    assert len(bot.sent) == 1
    assert events.subscriber_count() == before
    await notifications.shutdown_notifications()  # stopping twice is harmless


@pytest.mark.asyncio
async def test_a_slow_send_does_not_stop_the_bus_from_draining(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = asyncio.Event()
    started: list[str] = []

    async def _slow(entry: LogEntry) -> None:
        started.append(entry.event)
        await release.wait()

    monkeypatch.setattr(notifications, "handle_log_entry", _slow)
    await notifications.start_notifications()
    try:
        await log_event("INFO", "warming_started", account_id="acc-1")
        await log_event("INFO", "warming_target_reached", account_id="acc-2")
        for _ in range(100):
            if len(started) == 2:
                break
            await asyncio.sleep(0.01)
        # Both rows are being handled although the first one never returned.
        assert started == ["warming_started", "warming_target_reached"]
    finally:
        release.set()
        await notifications.shutdown_notifications()


@pytest.mark.asyncio
async def test_shutdown_lets_an_in_flight_send_finish(monkeypatch: pytest.MonkeyPatch) -> None:
    finished: list[str] = []
    entered = asyncio.Event()

    async def _send(entry: LogEntry) -> None:
        entered.set()
        await asyncio.sleep(0.05)
        finished.append(entry.event)

    monkeypatch.setattr(notifications, "handle_log_entry", _send)
    await notifications.start_notifications()
    await log_event("INFO", "warming_target_reached", account_id="acc-1")
    await asyncio.wait_for(entered.wait(), timeout=2)

    await notifications.shutdown_notifications()

    assert finished == ["warming_target_reached"]


@pytest.mark.asyncio
async def test_shutdown_cancels_a_send_past_the_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.notifications, "shutdown_grace_seconds", 0.01)
    entered = asyncio.Event()

    async def _stuck(_entry: LogEntry) -> None:
        entered.set()
        await asyncio.sleep(3600)

    monkeypatch.setattr(notifications, "handle_log_entry", _stuck)
    await notifications.start_notifications()
    await log_event("INFO", "warming_target_reached", account_id="acc-1")
    await asyncio.wait_for(entered.wait(), timeout=2)

    await asyncio.wait_for(notifications.shutdown_notifications(), timeout=2)

    assert not notifications._runtime.pending
