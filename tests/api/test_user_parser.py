"""``/user-parser``: a start's status, run reads, bases, presets and the export download."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import quote

import pytest

from core.repositories import user_parser as repository
from schemas.user_parser import UserParserRequest, UserParserStartOutcome
from schemas.user_parser_run import UserParserRun, UserParserUser
from services import user_parser as parser
from tests.api.accounts_helpers import client

if TYPE_CHECKING:
    from fastapi import FastAPI

_BASE = "/api/v1/user-parser"
_TOO_MANY = "user_parser_too_many_sources"
_BODY = {"mode": "members", "sources": ["@group"], "account_ids": ["a1"]}


async def _settled(run_id: str = "r1", users: list[UserParserUser] | None = None) -> None:
    run = UserParserRun(
        run_id=run_id, name="Крипта база", mode="members", status="running", created_at="2026"
    )
    await repository.create_run(run, UserParserRequest.model_validate(_BODY))
    await repository.settle_run(run.model_copy(update={"status": "done"}), users or [])


@pytest.mark.asyncio
async def test_a_start_answers_202_with_its_status(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[UserParserRequest] = []

    async def _start(request: UserParserRequest) -> UserParserStartOutcome:
        seen.append(request)
        return UserParserStartOutcome(status="account_busy", refused_account_id="a1")

    monkeypatch.setattr(parser, "start_run", _start)
    async with client(app) as http:
        started = await http.post(f"{_BASE}/runs", json=_BODY | {"keywords": ["shop"]})
        empty = await http.post(f"{_BASE}/runs", json=_BODY | {"sources": []})
        nobody = await http.post(f"{_BASE}/runs", json=_BODY | {"account_ids": []})
        unknown = await http.post(f"{_BASE}/runs", json=_BODY | {"bogus": 1})

    assert started.status_code == 202
    assert started.json() == {
        "status": "account_busy",
        "run_id": None,
        "refused_account_id": "a1",
    }
    assert seen[0].keywords == ["shop"]
    assert (empty.status_code, nobody.status_code, unknown.status_code) == (422, 422, 422)


@pytest.mark.asyncio
async def test_a_start_past_the_ceilings_is_422_with_a_code(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _start(_request: UserParserRequest) -> UserParserStartOutcome:
        raise parser.UserParserInvalidError(_TOO_MANY)

    monkeypatch.setattr(parser, "start_run", _start)
    async with client(app) as http:
        refused = await http.post(f"{_BASE}/runs", json=_BODY)

    assert refused.status_code == 422
    assert refused.json()["error"]["message"] == _TOO_MANY


@pytest.mark.asyncio
async def test_runs_are_read_stopped_and_paged(app: FastAPI) -> None:
    await _settled(users=[UserParserUser(user_id=i, message_count=i) for i in (1, 2, 3)])

    async with client(app) as http:
        run = await http.get(f"{_BASE}/runs/r1")
        stopped = await http.post(f"{_BASE}/runs/r1/stop")
        page = await http.get(f"{_BASE}/runs/r1/users", params={"offset": 1, "limit": 1})
        missing = await http.get(f"{_BASE}/runs/nope")
        stop_missing = await http.post(f"{_BASE}/runs/nope/stop")
        page_missing = await http.get(f"{_BASE}/runs/nope/users")
        too_big = await http.get(f"{_BASE}/runs/r1/users", params={"limit": 501})

    assert (run.status_code, run.json()["status"]) == (200, "done")
    assert stopped.json()["status"] == "done"
    assert (page.json()["total"], [u["user_id"] for u in page.json()["items"]]) == (3, [2])
    assert (missing.status_code, stop_missing.status_code, page_missing.status_code) == (
        404,
        404,
        404,
    )
    assert too_big.status_code == 422


@pytest.mark.asyncio
async def test_the_export_is_a_named_attachment(app: FastAPI) -> None:
    await _settled(users=[UserParserUser(user_id=7, first_name="Анна")])

    async with client(app) as http:
        csv_file = await http.get(f"{_BASE}/runs/r1/export")
        json_file = await http.get(f"{_BASE}/runs/r1/export", params={"format": "json"})
        missing = await http.get(f"{_BASE}/runs/nope/export")
        bad = await http.get(f"{_BASE}/runs/r1/export", params={"format": "xlsx"})

    assert csv_file.status_code == 200
    assert csv_file.headers["content-type"].startswith("text/csv")
    assert csv_file.headers["content-disposition"] == (
        f"attachment; filename*=UTF-8''{quote('Крипта_база.csv', safe='')}"
    )
    assert csv_file.content.startswith(b"\xef\xbb\xbf")
    assert "Анна" in csv_file.content.decode("utf-8-sig")
    assert json_file.json()[0]["user_id"] == 7
    assert (missing.status_code, bad.status_code) == (404, 422)


@pytest.mark.asyncio
async def test_bases_are_listed_renamed_searched_and_deleted(app: FastAPI) -> None:
    await _settled(users=[UserParserUser(user_id=1, first_name="Борис"), UserParserUser(user_id=2)])

    async with client(app) as http:
        listed = await http.get(f"{_BASE}/bases")
        renamed = await http.patch(f"{_BASE}/bases/r1", json={"name": "Новая"})
        blank = await http.patch(f"{_BASE}/bases/r1", json={"name": ""})
        rename_missing = await http.patch(f"{_BASE}/bases/nope", json={"name": "x"})
        found = await http.get(f"{_BASE}/bases/r1/users", params={"search": "борис"})
        deleted = await http.delete(f"{_BASE}/bases/r1")
        again = await http.delete(f"{_BASE}/bases/r1")

    assert [b["run_id"] for b in listed.json()["items"]] == ["r1"]
    assert renamed.json()["name"] == "Новая"
    assert (blank.status_code, rename_missing.status_code) == (422, 404)
    assert [u["user_id"] for u in found.json()["items"]] == [1]
    assert (deleted.status_code, again.status_code) == (204, 404)


@pytest.mark.asyncio
async def test_a_base_still_collecting_cannot_be_deleted(app: FastAPI) -> None:
    run = UserParserRun(run_id="live", name="x", mode="members", status="running", created_at="1")
    await repository.create_run(run, UserParserRequest.model_validate(_BODY))

    async with client(app) as http:
        refused = await http.delete(f"{_BASE}/bases/live")

    assert refused.status_code == 409
    assert refused.json()["error"]["message"] == "user_parser_run_running"


@pytest.mark.asyncio
async def test_presets_are_saved_listed_and_deleted(app: FastAPI) -> None:
    async with client(app) as http:
        created = await http.post(
            f"{_BASE}/presets", json={"name": " Комменты 1 ", "settings": {"mode": "comments"}}
        )
        clash = await http.post(f"{_BASE}/presets", json={"name": "комменты 1", "settings": {}})
        blank = await http.post(f"{_BASE}/presets", json={"name": "   ", "settings": {}})
        listed = await http.get(f"{_BASE}/presets")
        preset_id = created.json()["preset_id"]
        deleted = await http.delete(f"{_BASE}/presets/{preset_id}")
        again = await http.delete(f"{_BASE}/presets/{preset_id}")

    assert created.status_code == 200
    assert (created.json()["name"], created.json()["settings"]["mode"]) == (
        "Комменты 1",
        "comments",
    )
    assert clash.status_code == 409
    assert clash.json()["error"]["message"] == "user_parser_preset_name_taken"
    assert blank.status_code == 422
    assert [p["name"] for p in listed.json()["items"]] == ["Комменты 1"]
    assert (deleted.status_code, again.status_code) == (204, 404)


@pytest.mark.asyncio
async def test_the_account_picker_lists_the_fleet(app: FastAPI) -> None:
    async with client(app) as http:
        listed = await http.get(f"{_BASE}/accounts")

    assert listed.status_code == 200
    assert listed.json() == {"items": []}
