"""Outreach dispatch: the "typing…" a bulk-message run shows before each first message.

The ``outreach_*`` family enters through ``_action_families`` (one table row).
Classification stays in ``execute``: Telethon's own errors reach its ladder.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telethon.tl.functions.messages import SetTypingRequest
from telethon.tl.types import SendMessageTypingAction

from core.telegram_client._action_results import _DispatchResult
from schemas.telegram_actions_outreach import OutreachSetTyping

if TYPE_CHECKING:
    from telethon import TelegramClient

    from schemas.telegram_actions import TelegramAction


async def dispatch_outreach_action(
    client: TelegramClient, action: TelegramAction
) -> _DispatchResult:
    """Run one ``outreach_*`` write; the match narrows ``action`` for ty."""
    match action:
        case OutreachSetTyping():
            # The same peer rule as ``SendChatMessage``: decimal recipients are user ids.
            peer = int(action.recipient) if action.recipient.isdecimal() else action.recipient
            entity = await client.get_input_entity(peer)
            await client(SetTypingRequest(peer=entity, action=SendMessageTypingAction()))
            return _DispatchResult()
        case _:  # pragma: no cover - the family table routes only outreach_* here
            msg = f"Unsupported outreach action_type: {action.action_type}"
            raise ValueError(msg)


def outreach_log_extra(action: TelegramAction) -> dict[str, object]:
    match action:
        case OutreachSetTyping():
            return {"recipient": action.recipient}
        case _:  # pragma: no cover - the family table routes only outreach_* here
            return {}
