"""``GET /warming/survival`` forwards the cohort cut and refuses an unknown one."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from schemas.survival import SurvivalCohortKind, SurvivalCounter, SurvivalReport

if TYPE_CHECKING:
    from fastapi import FastAPI


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_the_cohort_is_forwarded_and_defaults_to_the_start_week(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[SurvivalCohortKind] = []

    async def fake(cohort: SurvivalCohortKind = "week") -> SurvivalReport:
        seen.append(cohort)
        return SurvivalReport(cohort=cohort, windows=[SurvivalCounter(window="d7", alive=3)])

    monkeypatch.setattr("services.survival.load_survival", fake)
    async with _client(app) as client:
        default = await client.get("/api/v1/warming/survival")
        persona = await client.get("/api/v1/warming/survival", params={"cohort": "persona"})

    assert (default.status_code, persona.status_code) == (200, 200)
    assert seen == ["week", "persona"]
    assert default.json()["windows"][0]["alive"] == 3


@pytest.mark.asyncio
async def test_an_unknown_cohort_is_refused(app: FastAPI) -> None:
    async with _client(app) as client:
        resp = await client.get("/api/v1/warming/survival", params={"cohort": "zodiac"})

    assert resp.status_code == 422
