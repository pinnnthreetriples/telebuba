"""Prefix-routed action families — the one arm ``_dispatch_action`` shares among them.

``_dispatch_action`` sits exactly at the complexity gate (cc 20), so a family that one
sibling module dispatches together enters through this table instead of a new arm;
``_action_log_extra`` reads the same table for its static log fields.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from core.telegram_client._channels import _channel_log_extra, _dispatch_channel_action
from core.telegram_client._warm_dispatch import dispatch_warming_action, warm_log_extra

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from telethon import TelegramClient

    from core.telegram_client._action_results import _DispatchResult
    from schemas.telegram_actions import TelegramAction


class _Family(NamedTuple):
    dispatch: Callable[[TelegramClient, TelegramAction], Awaitable[_DispatchResult]]
    log_extra: Callable[[TelegramAction], dict[str, object]]


_FAMILIES: dict[str, _Family] = {
    "channel_": _Family(_dispatch_channel_action, _channel_log_extra),
    "warm_": _Family(dispatch_warming_action, warm_log_extra),
}


def prefix_family(action_type: str) -> _Family | None:
    return next((f for prefix, f in _FAMILIES.items() if action_type.startswith(prefix)), None)
