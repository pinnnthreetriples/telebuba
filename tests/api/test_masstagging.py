"""``/masstagging`` routes — which refusal is which status, against the real service."""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING

import httpx
import pytest

from api import _large_upload_patterns
from core.config import settings
from core.repositories import masstagging as repository
from core.repositories import user_parser as user_parser_repository
from schemas.user_parser import UserParserRequest
from schemas.user_parser_run import UserParserRun, UserParserUser
from services import _account_owner
from services.masstagging import _runtime, _seams, _state
from tests.services.masstagging.fakes import Clock, FakeTelegram, png, seed

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from fastapi import FastAPI

_BASE = "/api/v1/masstagging"


@pytest.fixture(autouse=True)
def telegram(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[FakeTelegram]:
    _account_owner.reset_for_tests()
    _state.reset_for_tests()
    _runtime.reset_for_tests()
    clock = Clock()
    fake = FakeTelegram(clock=clock)

    async def _sleep(seconds: float) -> None:
        clock.advance(seconds)
        await asyncio.sleep(0)

    monkeypatch.setattr(_seams, "execute", fake.execute)
    monkeypatch.setattr(_seams, "execute_read", fake.execute_read)
    monkeypatch.setattr(_seams, "sleep", _sleep)
    monkeypatch.setattr(_seams, "now", clock.now)
    monkeypatch.setattr(settings.masstagging, "media_dir", tmp_path / "media")
    yield fake
    _account_owner.reset_for_tests()


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def test_the_media_and_import_uploads_are_on_the_large_upload_allowlist() -> None:
    patterns = [re.compile(pattern) for pattern in _large_upload_patterns()]

    def allowed(path: str) -> bool:
        return any(pattern.fullmatch(path) for pattern in patterns)

    assert allowed(f"{_BASE}/campaigns/c1/media")
    assert allowed(f"{_BASE}/campaigns/c1/targets/import")
    assert not allowed(f"{_BASE}/campaigns/c1/settings")
    assert not allowed(f"{_BASE}/campaigns/c1/targets/base")


@pytest.mark.asyncio
async def test_campaign_crud_settings_and_board(app: FastAPI) -> None:
    async with _client(app) as client:
        created = (await client.post(f"{_BASE}/campaigns", json={"name": "Spring"})).json()
        cid = created["campaign_id"]
        read = (await client.get(f"{_BASE}/campaigns/{cid}/settings")).json()
        saved = await client.put(
            f"{_BASE}/campaigns/{cid}/settings",
            json={
                "expected_updated_at": read["updated_at"],
                "name": "Spring wave",
                "account_ids": [],
                "settings": {"mode": "priming", "targets": ["@alpha", "t.me/beta"]},
            },
        )
        stale = await client.put(
            f"{_BASE}/campaigns/{cid}/settings",
            json={"expected_updated_at": read["updated_at"], "name": "x", "settings": {}},
        )
        invalid = await client.put(
            f"{_BASE}/campaigns/{cid}/settings",
            json={
                "expected_updated_at": saved.json()["updated_at"],
                "name": "x",
                "settings": {"targets": ["t.me/+invite"]},
            },
        )
        out_of_range = await client.put(
            f"{_BASE}/campaigns/{cid}/settings",
            json={
                "expected_updated_at": saved.json()["updated_at"],
                "name": "x",
                "settings": {"dialog_gap_seconds": {"min": 60, "max": 20}},
            },
        )
        board = await client.get(f"{_BASE}/campaigns/{cid}/board")
        listed = (await client.get(f"{_BASE}/campaigns")).json()
        deleted = await client.delete(f"{_BASE}/campaigns/{cid}")
        missing = await client.get(f"{_BASE}/campaigns/{cid}/board")

    assert saved.status_code == 200
    assert saved.json()["settings"]["targets"] == ["@alpha", "@beta"]
    assert (stale.status_code, stale.json()["error"]["message"]) == (409, "campaign_changed")
    assert (invalid.status_code, invalid.json()["error"]["message"]) == (
        400,
        "masstagging_invalid_target",
    )
    assert out_of_range.status_code == 422
    assert board.status_code == 200
    assert [row["target"] for row in board.json()["rows"]] == ["@alpha", "@beta"]
    assert listed["items"][0]["status"] == "draft"
    assert listed["items"][0]["missing"] == ["no_accounts"]
    assert deleted.status_code == 204
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_start_pause_and_the_refusals(app: FastAPI, telegram: FakeTelegram) -> None:
    cid = await seed()
    record = await repository.fetch_campaign(cid)
    assert record is not None
    stamp = record.updated_at
    telegram.hold = asyncio.Event()
    async with _client(app) as client:
        stale = await client.post(
            f"{_BASE}/campaigns/{cid}/start",
            json={"expected_updated_at": "2020-01-01T00:00:00+00:00"},
        )
        started = await client.post(
            f"{_BASE}/campaigns/{cid}/start", json={"expected_updated_at": stamp}
        )
        again = await client.post(
            f"{_BASE}/campaigns/{cid}/start",
            json={"expected_updated_at": started.json()["campaign"]["updated_at"]},
        )
        locked = await client.put(
            f"{_BASE}/campaigns/{cid}/settings",
            json={
                "expected_updated_at": started.json()["campaign"]["updated_at"],
                "name": "x",
                "settings": {},
            },
        )
        pace = await client.patch(f"{_BASE}/campaigns/{cid}/pace", json={"stories_per_day": 4})
        refused_delete = await client.delete(f"{_BASE}/campaigns/{cid}")
        stopped = await client.post(f"{_BASE}/campaigns/{cid}/stop")
        unknown = await client.post(f"{_BASE}/campaigns/nope/stop")

    assert (stale.status_code, stale.json()["error"]["message"]) == (409, "campaign_changed")
    assert started.status_code == 200
    assert started.json()["campaign"]["status"] == "running"
    assert (again.status_code, again.json()["error"]["message"]) == (409, "campaign_running")
    assert (locked.status_code, locked.json()["error"]["message"]) == (409, "campaign_running")
    assert pace.status_code == 200
    assert pace.json()["settings"]["stories_per_day"] == 4
    assert refused_delete.status_code == 409
    assert stopped.json()["campaign"]["status"] == "paused"
    assert unknown.status_code == 404


@pytest.mark.asyncio
async def test_start_refuses_a_campaign_that_lacks_media(app: FastAPI) -> None:
    cid = await seed(media=[])
    record = await repository.fetch_campaign(cid)
    assert record is not None
    stamp = record.updated_at
    async with _client(app) as client:
        refused = await client.post(
            f"{_BASE}/campaigns/{cid}/start", json={"expected_updated_at": stamp}
        )
        unknown = await client.post(
            f"{_BASE}/campaigns/nope/start", json={"expected_updated_at": stamp}
        )

    assert (refused.status_code, refused.json()["error"]["message"]) == (
        409,
        "masstagging_no_media",
    )
    assert unknown.status_code == 404


@pytest.mark.asyncio
async def test_board_actions(app: FastAPI) -> None:
    cid = await seed()
    from schemas.masstagging_records import TargetSeed  # noqa: PLC0415

    await repository.replace_targets(
        cid, [TargetSeed(target_key="u:alpha", raw="@alpha", kind="username")], at=1
    )
    async with _client(app) as client:
        skipped = await client.post(
            f"{_BASE}/campaigns/{cid}/targets/action",
            json={"target_key": "u:alpha", "action": "skip"},
        )
        moved = await client.post(
            f"{_BASE}/campaigns/{cid}/targets/action",
            json={"target_key": "u:alpha", "action": "now"},
        )
        unknown_target = await client.post(
            f"{_BASE}/campaigns/{cid}/targets/action",
            json={"target_key": "u:ghost", "action": "skip"},
        )
        unknown = await client.post(
            f"{_BASE}/campaigns/nope/targets/action",
            json={"target_key": "u:alpha", "action": "skip"},
        )
        bad = await client.post(
            f"{_BASE}/campaigns/{cid}/targets/action",
            json={"target_key": "u:alpha", "action": "hand"},
        )

    assert skipped.status_code == 200
    assert skipped.json()["rows"][0]["state"] == "skipped"
    assert (moved.status_code, moved.json()["error"]["message"]) == (
        409,
        "masstagging_target_state_changed",
    )
    assert unknown_target.json()["error"]["message"] == "masstagging_target_not_found"
    assert unknown.status_code == 404
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_media_upload(app: FastAPI) -> None:
    cid = await seed()
    async with _client(app) as client:
        stored = await client.post(
            f"{_BASE}/campaigns/{cid}/media", files={"file": ("me.png", png(), "image/png")}
        )
        refused = await client.post(
            f"{_BASE}/campaigns/{cid}/media", files={"file": ("me.txt", b"x", "text/plain")}
        )
        unknown = await client.post(
            f"{_BASE}/campaigns/nope/media", files={"file": ("me.png", png(), "image/png")}
        )

    assert stored.status_code == 200
    assert stored.json()["name"] == "me.png"
    assert (refused.status_code, refused.json()["error"]["message"]) == (
        400,
        "masstagging_media_invalid",
    )
    assert unknown.status_code == 404


@pytest.mark.asyncio
async def test_list_import_and_parser_base(app: FastAPI) -> None:
    cid = await seed()
    run = UserParserRun(run_id="b1", name="Base", mode="members", status="running", created_at="1")
    body = {"mode": "members", "sources": ["@group"], "account_ids": ["a1"]}
    await user_parser_repository.create_run(run, UserParserRequest.model_validate(body))
    await user_parser_repository.settle_run(
        run.model_copy(update={"status": "done"}), [UserParserUser(user_id=1, username="zed")]
    )
    async with _client(app) as client:
        imported = await client.post(
            f"{_BASE}/campaigns/{cid}/targets/import",
            files={"file": ("list.txt", b"@alpha\nt.me/+bad x\n+79001234567", "text/plain")},
        )
        not_text = await client.post(
            f"{_BASE}/campaigns/{cid}/targets/import",
            files={"file": ("list.txt", b"\xff\xfe\x00", "text/plain")},
        )
        base = await client.post(f"{_BASE}/campaigns/{cid}/targets/base", json={"base_id": "b1"})
        no_base = await client.post(
            f"{_BASE}/campaigns/{cid}/targets/base", json={"base_id": "nope"}
        )
        unknown = await client.post(f"{_BASE}/campaigns/nope/targets/base", json={"base_id": "b1"})

    assert imported.json() == {
        "targets": ["@alpha", "+79001234567"],
        "skipped": 2,
        "name": "",
    }
    assert (not_text.status_code, not_text.json()["error"]["message"]) == (
        400,
        "masstagging_import_invalid",
    )
    assert base.json() == {"targets": ["@zed"], "skipped": 0, "name": "Base"}
    assert (no_base.status_code, no_base.json()["error"]["message"]) == (
        400,
        "masstagging_base_not_found",
    )
    assert unknown.status_code == 404
