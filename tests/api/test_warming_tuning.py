"""``GET/PUT /warming/tuning`` — the «Темп прогрева» presets (Прогрев 2.0, item 5.2)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from core.config import settings

if TYPE_CHECKING:
    from fastapi import FastAPI

_URL = "/api/v1/warming/tuning"
_CUSTOM = {"intro": 5, "settling": 8, "warming": 12, "active": 20, "warmed": 40}


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_a_fresh_install_runs_the_configured_engine(app: FastAPI) -> None:
    async with _client(app) as client:
        resp = await client.get(_URL)

    assert resp.status_code == 200
    body = resp.json()
    assert body["preset"] == "normal"
    assert body["daily_caps"] == settings.warming.phase_daily_cap
    assert (body["pause_factor"], body["dm_factor"]) == (1.0, 1.0)
    # Visits a day for every persona in every phase, as the start dialog's hint.
    assert len(body["cadence"]) == 15
    assert body["warnings"] == []


@pytest.mark.asyncio
async def test_a_preset_is_one_click_and_the_board_hint_follows(app: FastAPI) -> None:
    async with _client(app) as client:
        careful = await client.put(_URL, json={"preset": "careful"})
        board = await client.get("/api/v1/warming/board")
        again = await client.get(_URL)

    assert careful.status_code == 200
    assert careful.json()["daily_caps"] == settings.warming.tuning_careful_daily_cap
    assert again.json()["preset"] == "careful"
    assert board.json()["cadence_preview"] == careful.json()["cadence"]


@pytest.mark.asyncio
async def test_custom_limits_are_kept_and_low_ones_are_flagged(app: FastAPI) -> None:
    async with _client(app) as client:
        custom = await client.put(
            _URL, json={"preset": "custom", "custom": {"daily_caps": _CUSTOM}}
        )
        normal = await client.put(_URL, json={"preset": "normal"})

    assert custom.status_code == 200
    assert custom.json()["daily_caps"] == _CUSTOM
    assert custom.json()["warnings"] == [
        {"code": "low_daily_cap", "phase": "intro", "daily_cap": 5},
        {"code": "low_daily_cap", "phase": "settling", "daily_cap": 8},
    ]
    # Back on «Обычно», the operator's own limits wait for the next «Свой».
    assert normal.json()["preset"] == "normal"
    assert normal.json()["custom"]["daily_caps"] == _CUSTOM


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"preset": "custom"},
        {"preset": "custom", "custom": {"daily_caps": {**_CUSTOM, "warming": 7}}},
        {"preset": "custom", "custom": {"daily_caps": {**_CUSTOM, "warmed": 81}}},
        {"preset": "custom", "custom": {"daily_caps": {**_CUSTOM, "intro": 0}}},
        {"preset": "custom", "custom": {"daily_caps": {"intro": 10}}},
        {"preset": "turbo"},
    ],
    ids=["no-limits", "dropping", "over-80", "zero", "missing-phase", "unknown-preset"],
)
async def test_invalid_limits_are_refused(app: FastAPI, body: dict[str, object]) -> None:
    async with _client(app) as client:
        resp = await client.put(_URL, json=body)
        after = await client.get(_URL)

    assert resp.status_code == 422
    assert after.json()["preset"] == "normal"
