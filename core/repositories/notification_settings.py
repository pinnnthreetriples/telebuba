"""Operator-bot notification settings — the singleton ``notification_settings`` row.

Keep/clear/replace like the LLM keys in ``_warming_settings``: ``None`` keeps a stored
value, ``""`` clears it, anything else replaces it. The row is created on first save;
before that a read answers the defaults (off, no token, no chat).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core._schema_notifications import _notification_settings
from core.db import _get_engine, _now_iso
from schemas.notifications import NotificationSettingsSecret

if TYPE_CHECKING:
    from collections.abc import Mapping

_SETTINGS_ID = 1


def _row_to_secret(mapping: Mapping[str, object] | None) -> NotificationSettingsSecret:
    if mapping is None:
        return NotificationSettingsSecret()
    return NotificationSettingsSecret(
        enabled=bool(mapping["enabled"]),
        bot_token=str(mapping["bot_token"] or ""),
        chat_id=str(mapping["chat_id"] or ""),
        updated_at=str(mapping["updated_at"]),
    )


def _read_row() -> Mapping[str, object] | None:
    table = _notification_settings
    with _get_engine().connect() as connection:
        row = connection.execute(select(table).where(table.c.id == _SETTINGS_ID)).mappings().first()
    return cast("Mapping[str, object] | None", row)


async def load_notification_settings() -> NotificationSettingsSecret:
    """The stored settings, token included; defaults when nothing was saved yet."""
    return _row_to_secret(await asyncio.to_thread(_read_row))


def _save(enabled: bool | None, bot_token: str | None, chat_id: str | None) -> None:  # noqa: FBT001
    # Only the fields the caller sent are written, in one statement: a read-then-write
    # would let an enabled-only save put back a stale token another save just replaced.
    provided: dict[str, object] = {"updated_at": _now_iso()}
    if enabled is not None:
        provided["enabled"] = int(enabled)
    if bot_token is not None:
        provided["bot_token"] = bot_token
    if chat_id is not None:
        provided["chat_id"] = chat_id
    defaults: dict[str, object] = {"enabled": 0, **dict.fromkeys(("bot_token", "chat_id"), "")}
    statement = sqlite_insert(_notification_settings).values(
        id=_SETTINGS_ID, **{**defaults, **provided}
    )
    statement = statement.on_conflict_do_update(
        index_elements=[_notification_settings.c.id], set_=provided
    )
    with _get_engine().begin() as connection:
        connection.execute(statement)


async def save_notification_settings(
    *,
    enabled: bool | None,
    bot_token: str | None,
    chat_id: str | None,
) -> NotificationSettingsSecret:
    """Upsert the row with keep/clear/replace semantics and return what is stored."""
    await asyncio.to_thread(_save, enabled, bot_token, chat_id)
    return await load_notification_settings()
