"""Operator-bot gateway — one Telegram Bot API ``sendMessage`` call over httpx.

The only module that talks to ``api.telegram.org``. It never raises for a delivery
failure: the caller gets a :class:`BotSendResult` and decides what to log. The bot
token sits in the URL path, and httpx quotes the request URL in some error messages,
so nothing here logs, and the verdict carries only an exception class name or the
HTTP status — never ``str(exc)``, never the URL.

A fresh client per call: messages are rare (throttled per account and kind), so a
pooled connection would mostly sit idle and need its own shutdown step.
"""

from __future__ import annotations

import httpx

from core.config import settings
from schemas.notifications import BotMessage, BotSendResult

_HTTP_SERVER_ERROR_MIN = 500


async def send_bot_message(message: BotMessage) -> BotSendResult:
    """Deliver ``message`` and say how it went; any failure becomes a verdict."""
    config = settings.notifications
    url = f"{config.bot_api_base_url}/bot{message.bot_token}/sendMessage"
    payload = {
        "chat_id": message.chat_id,
        "text": message.text,
        "disable_web_page_preview": True,
    }
    try:
        async with httpx.AsyncClient(timeout=config.send_timeout_seconds) as client:
            response = await client.post(url, json=payload)
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        return BotSendResult(status="unreachable", error_type=type(exc).__name__)
    if response.is_success:
        return BotSendResult(status="sent")
    # A Telegram-side outage is the network's kind of failure, not a refusal of ours.
    status = "unreachable" if response.status_code >= _HTTP_SERVER_ERROR_MIN else "rejected"
    return BotSendResult(status=status, error_type=f"http_{response.status_code}")
