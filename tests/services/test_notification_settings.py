"""Operator-bot settings: the token is masked on the way out; the test send's outcomes."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.db import list_recent_logs
from core.repositories.notification_settings import (
    load_notification_settings,
    save_notification_settings,
)
from schemas.notifications import (
    BotMessage,
    BotSendResult,
    NotificationSettingsSecret,
    NotificationSettingsUpdate,
)
from services import notification_settings

if TYPE_CHECKING:
    from schemas.notifications import BotSendStatus

_TOKEN = "123456:AAH-secret_token_value_abcdefXYZ"


async def _all_logs_text() -> str:
    return " ".join(row.model_dump_json() for row in await list_recent_logs())


@pytest.mark.asyncio
async def test_responses_carry_only_a_mask_of_the_token() -> None:
    saved = await notification_settings.save_settings(
        NotificationSettingsUpdate(enabled=True, bot_token=_TOKEN, chat_id="-100500"),
    )
    loaded = await notification_settings.load_settings()
    for view in (saved, loaded):
        assert view.has_bot_token is True
        assert view.bot_token_hint == "1234…fXYZ"
        assert _TOKEN not in view.model_dump_json()
        assert (view.enabled, view.chat_id) == (True, "-100500")
    assert _TOKEN not in await _all_logs_text()


@pytest.mark.asyncio
async def test_clearing_wins_over_a_typed_token_and_blank_keeps_it() -> None:
    await notification_settings.save_settings(NotificationSettingsUpdate(bot_token=_TOKEN))
    kept = await notification_settings.save_settings(NotificationSettingsUpdate(enabled=True))
    assert kept.has_bot_token is True
    cleared = await notification_settings.save_settings(
        NotificationSettingsUpdate(bot_token=_TOKEN, clear_bot_token=True),
    )
    assert (cleared.has_bot_token, cleared.bot_token_hint) == (False, None)


def test_a_short_token_hint_reveals_nothing() -> None:
    secret = NotificationSettingsSecret(bot_token="1:abc")
    assert notification_settings.mask_notification_settings(secret).bot_token_hint == "••••"


@pytest.mark.parametrize("token", ["123:short", "abc:" + "x" * 30, "1:" + "x" * 25 + "/../x"])
def test_a_malformed_token_is_refused_before_it_reaches_a_url(token: str) -> None:
    with pytest.raises(ValueError, match="bot_token"):
        NotificationSettingsUpdate(bot_token=token)


@pytest.mark.parametrize("chat_id", ["12 34", "@a", "chat"])
def test_a_malformed_chat_id_is_refused(chat_id: str) -> None:
    with pytest.raises(ValueError, match="chat_id"):
        NotificationSettingsUpdate(chat_id=chat_id)


@pytest.mark.asyncio
async def test_the_test_send_needs_a_token_and_a_chat() -> None:
    with pytest.raises(notification_settings.NotificationsNotConfiguredError):
        await notification_settings.send_test_notification()
    await notification_settings.save_settings(NotificationSettingsUpdate(bot_token=_TOKEN))
    with pytest.raises(notification_settings.NotificationsNotConfiguredError):
        await notification_settings.send_test_notification()


@pytest.mark.asyncio
async def test_the_test_send_works_while_notifications_are_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[BotMessage] = []

    async def _send(message: BotMessage) -> BotSendResult:
        sent.append(message)
        return BotSendResult(status="sent")

    monkeypatch.setattr(notification_settings, "send_bot_message", _send)
    await notification_settings.save_settings(
        NotificationSettingsUpdate(enabled=False, bot_token=_TOKEN, chat_id="42"),
    )
    result = await notification_settings.send_test_notification()
    assert result.sent is True
    assert [(m.bot_token, m.chat_id) for m in sent] == [(_TOKEN, "42")]
    assert "тестовое уведомление" in sent[0].text


@pytest.mark.parametrize("status", ["rejected", "unreachable"])
@pytest.mark.asyncio
async def test_a_failed_test_send_raises_its_status_and_logs_no_token(
    monkeypatch: pytest.MonkeyPatch,
    status: BotSendStatus,
) -> None:
    async def _send(_message: BotMessage) -> BotSendResult:
        return BotSendResult(status=status, error_type="http_401")

    monkeypatch.setattr(notification_settings, "send_bot_message", _send)
    await notification_settings.save_settings(
        NotificationSettingsUpdate(bot_token=_TOKEN, chat_id="42"),
    )
    with pytest.raises(notification_settings.NotificationDeliveryError) as caught:
        await notification_settings.send_test_notification()
    assert caught.value.status == status
    rows = [row for row in await list_recent_logs() if row.event == "notifications_test_failed"]
    assert rows[0].extra == {"status": status, "error_type": "http_401"}
    assert _TOKEN not in await _all_logs_text()


@pytest.mark.asyncio
async def test_an_enabled_only_save_keeps_a_token_saved_since_it_was_read() -> None:
    await save_notification_settings(
        enabled=None, bot_token="111111:old_token_value_abcdefghij", chat_id="42"
    )
    # Another save replaces the token; an enabled-only save that follows writes only
    # ``enabled``, so it cannot put the old token back.
    await save_notification_settings(enabled=None, bot_token=_TOKEN, chat_id=None)
    await save_notification_settings(enabled=True, bot_token=None, chat_id=None)

    stored = await load_notification_settings()
    assert stored.enabled is True
    assert stored.bot_token == _TOKEN
    assert stored.chat_id == "42"
