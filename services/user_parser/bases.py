"""Bases («Базы»), the people in them, their export, and presets.

A base is a settled run. Nothing removes one but the operator; deleting it deletes its
people too. The export streams the base in chunks, so a large base never sits in memory
whole.
"""

from __future__ import annotations

import csv
import io
import re
from typing import TYPE_CHECKING

from core.config import settings
from core.repositories import user_parser as repository

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from schemas.user_parser import UserParserPreset, UserParserPresetList, UserParserSettings
    from schemas.user_parser_run import (
        ExportFormat,
        UserParserBase,
        UserParserBaseList,
        UserParserUser,
        UserParserUserPage,
    )

_CSV_COLUMNS = (
    "id",
    "username",
    "first_name",
    "last_name",
    "premium",
    "photo",
    "stories",
    "bot",
    "deleted",
    "scam",
    "fake",
    "last_seen",
    "count",
    "first_at",
    "last_at",
    "sources",
)
_MEDIA_TYPES: dict[ExportFormat, str] = {
    "csv": "text/csv; charset=utf-8",
    "json": "application/json",
}
# Excel opens a UTF-8 CSV as UTF-8 only when the file starts with the byte-order mark.
_BOM = chr(0xFEFF)
_UNSAFE_NAME = re.compile(r"[^\w]+", re.UNICODE)


class PresetNameTakenError(ValueError):
    """Another preset already has this name, ignoring case."""


class BaseRunningError(ValueError):
    """The run behind this base is still collecting."""


async def list_bases() -> UserParserBaseList:
    return await repository.list_bases()


async def rename_base(run_id: str, name: str) -> UserParserBase | None:
    return await repository.rename_base(run_id, name)


async def delete_base(run_id: str) -> bool:
    """Delete a base and its people; ``False`` when unknown. A running one is refused."""
    miss = await repository.delete_base(run_id)
    if miss == "running":
        raise BaseRunningError(run_id)
    return miss is None


async def list_base_users(
    run_id: str, *, search: str, offset: int, limit: int
) -> UserParserUserPage | None:
    if await repository.fetch_run(run_id) is None:
        return None
    limit = min(limit, settings.user_parser.max_page_rows)
    return await repository.page_users(run_id, search=search, offset=offset, limit=limit)


def _csv_row(user: UserParserUser) -> list[object]:
    return [
        user.user_id,
        user.username or "",
        user.first_name,
        user.last_name,
        int(user.is_premium),
        int(user.has_photo),
        int(user.has_stories),
        int(user.is_bot),
        int(user.is_deleted),
        int(user.is_scam),
        int(user.is_fake),
        user.last_seen,
        user.message_count,
        user.first_at or "",
        user.last_at or "",
        " ".join(user.sources),
    ]


def _csv_chunk(users: list[UserParserUser], *, head: bool) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    if head:
        buffer.write(_BOM)
        writer.writerow(_CSV_COLUMNS)
    writer.writerows(_csv_row(user) for user in users)
    return buffer.getvalue().encode("utf-8")


def _json_chunk(users: list[UserParserUser], *, head: bool) -> bytes:
    body = ",\n".join(user.model_dump_json() for user in users)
    return (("[\n" if head else ",\n") + body).encode("utf-8") if body else b""


async def _chunks(run_id: str, fmt: ExportFormat) -> AsyncIterator[bytes]:
    after: int | None = None
    head = True
    size = settings.user_parser.export_chunk_rows
    while True:
        users = await repository.users_after(run_id, after, size)
        if fmt == "csv":
            yield _csv_chunk(users, head=head)
        else:
            yield _json_chunk(users, head=head) if users else (b"[" if head else b"")
        if len(users) < size:
            break
        head = False
        after = users[-1].user_id
    if fmt == "json":
        yield b"\n]\n"


def export_filename(name: str, fmt: ExportFormat) -> str:
    stem = _UNSAFE_NAME.sub("_", name).strip("_") or "users"
    return f"{stem}.{fmt}"


async def export_base(
    run_id: str, fmt: ExportFormat
) -> tuple[str, str, AsyncIterator[bytes]] | None:
    """``(filename, media type, body chunks)`` for a base, or ``None`` when unknown."""
    run = await repository.fetch_run(run_id)
    if run is None:
        return None
    return export_filename(run.name, fmt), _MEDIA_TYPES[fmt], _chunks(run_id, fmt)


async def list_presets() -> UserParserPresetList:
    return await repository.list_presets()


async def create_preset(name: str, values: UserParserSettings) -> UserParserPreset:
    created = await repository.create_preset(name, values)
    if created == "name_taken":
        raise PresetNameTakenError(name)
    return created


async def delete_preset(preset_id: str) -> bool:
    return await repository.delete_preset(preset_id)
