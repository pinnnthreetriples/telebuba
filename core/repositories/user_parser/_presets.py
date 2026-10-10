"""User-parser presets: the form saved under a name, names unique ignoring case."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from sqlalchemy import delete, insert, select
from sqlalchemy.exc import IntegrityError

from core.db import _get_engine, _now_iso
from core.repositories.user_parser._tables import _user_parser_presets as _presets
from schemas.user_parser import UserParserPreset, UserParserPresetList, UserParserSettings

if TYPE_CHECKING:
    from sqlalchemy import RowMapping

PresetMiss = Literal["name_taken"]


def _preset(row: RowMapping) -> UserParserPreset:
    return UserParserPreset(
        preset_id=str(row["preset_id"]),
        name=str(row["name"]),
        settings=UserParserSettings.model_validate_json(row["settings_json"]),
        created_at=str(row["created_at"]),
    )


def _list_presets() -> UserParserPresetList:
    statement = select(_presets).order_by(_presets.c.created_at.asc(), _presets.c.name.asc())
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return UserParserPresetList(items=[_preset(row) for row in rows])


async def list_presets() -> UserParserPresetList:
    return await asyncio.to_thread(_list_presets)


def _create_preset(name: str, settings: UserParserSettings) -> UserParserPreset | PresetMiss:
    preset = UserParserPreset(
        preset_id=uuid4().hex, name=name, settings=settings, created_at=_now_iso()
    )
    try:
        with _get_engine().begin() as connection:
            connection.execute(
                insert(_presets).values(
                    preset_id=preset.preset_id,
                    name=name,
                    name_key=name.casefold(),
                    settings_json=settings.model_dump_json(),
                    created_at=preset.created_at,
                )
            )
    except IntegrityError:
        return "name_taken"
    return preset


async def create_preset(name: str, settings: UserParserSettings) -> UserParserPreset | PresetMiss:
    return await asyncio.to_thread(_create_preset, name, settings)


def _delete_preset(preset_id: str) -> bool:
    with _get_engine().begin() as connection:
        return bool(
            connection.execute(delete(_presets).where(_presets.c.preset_id == preset_id)).rowcount
        )


async def delete_preset(preset_id: str) -> bool:
    return await asyncio.to_thread(_delete_preset, preset_id)
