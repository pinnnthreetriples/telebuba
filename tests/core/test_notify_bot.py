"""The operator-bot gateway (``core.notify_bot``): verdicts, never exceptions, never the token."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from core.config import settings
from core.notify_bot import send_bot_message
from schemas.notifications import BotMessage

_TOKEN = "123456:AAH-secret_token_value_abcdefXYZ"
_URL = f"https://api.telegram.org/bot{_TOKEN}/sendMessage"


def _message() -> BotMessage:
    return BotMessage(bot_token=_TOKEN, chat_id="-100500", text="hello")


@pytest.mark.asyncio
async def test_a_delivered_message_reports_sent_with_the_chat_and_text() -> None:
    with respx.mock:
        route = respx.post(_URL).mock(return_value=httpx.Response(200, json={"ok": True}))
        result = await send_bot_message(_message())
    assert result.status == "sent"
    assert result.error_type is None
    body = json.loads(route.calls.last.request.content)
    assert body == {"chat_id": "-100500", "text": "hello", "disable_web_page_preview": True}


@pytest.mark.asyncio
async def test_the_base_url_comes_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.notifications, "bot_api_base_url", "https://bots.example")
    with respx.mock:
        route = respx.post(f"https://bots.example/bot{_TOKEN}/sendMessage").mock(
            return_value=httpx.Response(200, json={"ok": True}),
        )
        await send_bot_message(_message())
    assert route.called


@pytest.mark.parametrize("status_code", [400, 401, 403, 404])
@pytest.mark.asyncio
async def test_a_telegram_refusal_is_rejected_by_status(status_code: int) -> None:
    with respx.mock:
        respx.post(_URL).mock(return_value=httpx.Response(status_code, json={"ok": False}))
        result = await send_bot_message(_message())
    assert result.status == "rejected"
    assert result.error_type == f"http_{status_code}"


@pytest.mark.asyncio
async def test_a_telegram_outage_counts_as_unreachable() -> None:
    with respx.mock:
        respx.post(_URL).mock(return_value=httpx.Response(502))
        result = await send_bot_message(_message())
    assert result.status == "unreachable"
    assert result.error_type == "http_502"


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError(f"cannot connect to {_URL}"),
        httpx.ReadTimeout(f"timed out on {_URL}"),
    ],
)
@pytest.mark.asyncio
async def test_a_network_failure_is_a_verdict_without_the_token(error: Exception) -> None:
    with respx.mock:
        respx.post(_URL).mock(side_effect=error)
        result = await send_bot_message(_message())
    assert result.status == "unreachable"
    assert result.error_type == type(error).__name__
    assert _TOKEN not in result.model_dump_json()


def test_the_token_is_kept_out_of_the_message_repr() -> None:
    assert _TOKEN not in repr(_message())
