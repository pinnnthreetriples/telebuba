"""@SpamBot probe + self-restriction read for one account."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.config import settings
from core.logging import log_event
from core.telegram_client._client import telegram_client
from core.telegram_client._util import optional_str
from schemas.device_fingerprint import TelegramClientRequest
from schemas.spam_status import SpamStatusProbe

if TYPE_CHECKING:
    from telethon import TelegramClient

_SPAMBOT_USERNAME = "SpamBot"


async def check_spam_status(account_id: str) -> SpamStatusProbe:
    """Probe @SpamBot and read self-restriction flags for an account.

    Sends ``/start`` to @SpamBot and captures its reply, and reads the account's
    own ``restricted`` / ``restriction_reason`` flags via ``get_me``. The raw
    result is parsed and cached by ``services.spam_status`` — never raises.
    """
    request = TelegramClientRequest(account_id=account_id)
    async with telegram_client(request) as client:
        try:
            await client.connect()
            reply_text = await _probe_spambot(client)
            restricted, reason = await _probe_self_restriction(client)
        except Exception as exc:  # noqa: BLE001 - any probe failure classifies as unknown.
            await log_event(
                "WARNING",
                "telegram_spam_status_probe_failed",
                account_id=account_id,
                extra={"error_type": type(exc).__name__, "message": str(exc)},
            )
            return SpamStatusProbe(account_id=account_id, error=f"{type(exc).__name__}: {exc}")
    return SpamStatusProbe(
        account_id=account_id,
        reply_text=reply_text,
        restricted=restricted,
        restriction_reason=reason,
    )


async def _probe_spambot(client: TelegramClient) -> str | None:
    """Open a conversation with @SpamBot, send ``/start`` and return its reply."""
    async with client.conversation(
        _SPAMBOT_USERNAME,
        timeout=settings.telegram.timeout_seconds,
    ) as conv:
        await conv.send_message("/start")
        response = await conv.get_response()
        return optional_str(getattr(response, "text", None))


async def _probe_self_restriction(client: TelegramClient) -> tuple[bool, str | None]:
    """Read the account's own ``restricted`` flag + reason (terms/country)."""
    me = await client.get_me()
    restricted = bool(getattr(me, "restricted", False))
    reasons = getattr(me, "restriction_reason", None) or []
    reason = "; ".join(
        str(getattr(item, "text", "") or getattr(item, "reason", "")) for item in reasons
    ).strip("; ")
    return restricted, (reason or None)
