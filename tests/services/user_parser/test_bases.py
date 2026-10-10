"""Bases: export as CSV (with BOM) and JSON in chunks, pages, delete, presets."""

from __future__ import annotations

import csv
import io
import json
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.repositories import user_parser as repository
from schemas.user_parser import UserParserSettings
from schemas.user_parser_run import UserParserRun, UserParserUser
from services.user_parser import (
    BaseRunningError,
    PresetNameTakenError,
    create_preset,
    delete_base,
    export_base,
    list_base_users,
    list_bases,
    list_presets,
    rename_base,
)
from services.user_parser.bases import export_filename
from tests.services.user_parser.helpers import parser_request

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


async def _base(run_id: str, users: list[UserParserUser], *, status: str = "done") -> None:
    run = UserParserRun(
        run_id=run_id, name="Крипта: чат #1", mode="messages", status="running", created_at="2026"
    )
    await repository.create_run(run, parser_request())
    if status != "running":
        await repository.settle_run(run.model_copy(update={"status": status}), users)


async def _body(chunks: AsyncIterator[bytes]) -> bytes:
    return b"".join([chunk async for chunk in chunks])


_PEOPLE = [
    UserParserUser(
        user_id=1,
        username="anna",
        first_name='Анна "А"',
        is_premium=True,
        last_seen="week",
        message_count=3,
        sources=["@a", "@b"],
    ),
    UserParserUser(user_id=2, first_name="Bob, Jr", message_count=1, sources=["@a"]),
    UserParserUser(user_id=3, first_name="Cid"),
]


@pytest.mark.asyncio
async def test_csv_opens_in_excel_and_round_trips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.user_parser, "export_chunk_rows", 2)
    await _base("r1", _PEOPLE)

    export = await export_base("r1", "csv")
    assert export is not None
    filename, media_type, chunks = export
    body = await _body(chunks)

    assert filename == "Крипта_чат_1.csv"
    assert media_type.startswith("text/csv")
    assert body.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(body.decode("utf-8-sig"))))
    assert rows[0][:3] == ["id", "username", "first_name"]
    assert [row[0] for row in rows[1:]] == ["1", "2", "3"]
    assert rows[1][2] == 'Анна "А"'
    assert rows[1][-1] == "@a @b"
    assert rows[2][2] == "Bob, Jr"


@pytest.mark.asyncio
@pytest.mark.parametrize("chunk", [1, 2, 3, 1000])
async def test_json_is_one_valid_array_whatever_the_chunking(
    monkeypatch: pytest.MonkeyPatch, chunk: int
) -> None:
    monkeypatch.setattr(settings.user_parser, "export_chunk_rows", chunk)
    await _base("r1", _PEOPLE)

    export = await export_base("r1", "json")
    assert export is not None
    _name, media_type, chunks = export

    assert media_type == "application/json"
    parsed = json.loads(await _body(chunks))
    assert [item["user_id"] for item in parsed] == [1, 2, 3]
    assert parsed[0]["sources"] == ["@a", "@b"]


@pytest.mark.asyncio
async def test_an_empty_base_exports_a_header_or_an_empty_array() -> None:
    await _base("r1", [])

    csv_export = await export_base("r1", "csv")
    json_export = await export_base("r1", "json")
    assert csv_export is not None
    assert json_export is not None

    assert (await _body(csv_export[2])).decode("utf-8-sig").startswith("id,")
    assert json.loads(await _body(json_export[2])) == []
    assert await export_base("missing", "csv") is None


def test_an_unusable_name_still_gives_a_file_name() -> None:
    assert export_filename("!!!", "json") == "users.json"


@pytest.mark.asyncio
async def test_bases_pages_rename_and_delete() -> None:
    await _base("r1", _PEOPLE)
    await _base("live", [], status="running")

    assert [b.run_id for b in (await list_bases()).items] == ["r1"]
    page = await list_base_users("r1", search="bob", offset=0, limit=10)
    assert page is not None
    assert [u.user_id for u in page.items] == [2]
    assert await list_base_users("missing", search="", offset=0, limit=10) is None

    renamed = await rename_base("r1", "Новое имя")
    assert renamed is not None
    assert renamed.name == "Новое имя"

    with pytest.raises(BaseRunningError):
        await delete_base("live")
    assert await delete_base("r1") is True
    assert await delete_base("r1") is False


@pytest.mark.asyncio
async def test_a_page_never_exceeds_the_configured_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.user_parser, "max_page_rows", 2)
    await _base("r1", _PEOPLE)

    page = await list_base_users("r1", search="", offset=0, limit=500)

    assert page is not None
    assert (len(page.items), page.total) == (2, 3)


@pytest.mark.asyncio
async def test_preset_names_are_unique() -> None:
    await create_preset("Комменты 1", UserParserSettings(mode="comments"))

    with pytest.raises(PresetNameTakenError):
        await create_preset("комменты 1", UserParserSettings())

    assert [p.name for p in (await list_presets()).items] == ["Комменты 1"]


@pytest.mark.asyncio
async def test_names_a_spreadsheet_would_run_are_defused() -> None:
    await _base(
        "r1",
        [
            UserParserUser(user_id=1, first_name='=HYPERLINK("http://x")', username="@x"),
            UserParserUser(user_id=2, first_name="-1+2", last_name="+7", sources=["@a"]),
        ],
    )

    export = await export_base("r1", "csv")
    assert export is not None
    rows = list(csv.reader(io.StringIO((await _body(export[2])).decode("utf-8-sig"))))

    assert rows[1][1:3] == ["'@x", '\'=HYPERLINK("http://x")']
    assert rows[2][2:4] == ["'-1+2", "'+7"]
    assert rows[2][-1] == "@a"
