"""Mobile-container endpoints: the switch, the 409 for a second start, refusal codes."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from schemas.emulator import MobileOpenProgress, OpenMobileResult
from services.accounts import EmulatorBusyError, EmulatorRefusedError
from tests.api.accounts_helpers import client as _client

if TYPE_CHECKING:
    from fastapi import FastAPI


@pytest.mark.asyncio
async def test_the_status_reports_the_switch(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    async with _client(app) as client:
        off = await client.get("/api/v1/accounts/emulator")
        monkeypatch.setattr(settings.emulator, "enabled", True)
        on = await client.get("/api/v1/accounts/emulator")

    assert off.json() == {"enabled": False}
    assert on.json() == {"enabled": True}


@pytest.mark.asyncio
async def test_a_switched_off_emulator_answers_400_with_its_code(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _refuse(account_id: str) -> OpenMobileResult:  # noqa: ARG001
        code = "mobile_emulator_disabled"
        raise EmulatorRefusedError(code)

    monkeypatch.setattr("services.accounts.open_account_mobile", _refuse)
    async with _client(app) as client:
        resp = await client.post("/api/v1/accounts/acc-1/open-mobile")

    assert resp.status_code == 400
    assert resp.json()["error"]["message"] == "mobile_emulator_disabled"


@pytest.mark.asyncio
async def test_two_concurrent_starts_for_one_account_answer_409_to_the_second(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release, entered = asyncio.Event(), asyncio.Event()
    busy: set[str] = set()

    async def _open(account_id: str) -> OpenMobileResult:
        if account_id in busy:
            raise EmulatorBusyError
        busy.add(account_id)
        entered.set()
        await release.wait()
        return OpenMobileResult(account_id=account_id, status="started", signed_in=True)

    monkeypatch.setattr("services.accounts.open_account_mobile", _open)
    async with _client(app) as client:
        first = asyncio.create_task(client.post("/api/v1/accounts/acc-1/open-mobile"))
        await entered.wait()
        second = await client.post("/api/v1/accounts/acc-1/open-mobile")
        release.set()
        done = await first

    assert done.status_code == 200
    assert done.json() == {
        "account_id": "acc-1",
        "status": "started",
        "signed_in": True,
        "sign_in_refusal": None,
    }
    assert second.status_code == 409
    assert second.json()["error"]["message"] == "emulator_busy"


@pytest.mark.asyncio
async def test_the_progress_of_an_open_is_served_for_the_dialog(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _progress(account_id: str) -> MobileOpenProgress:
        return MobileOpenProgress(
            account_id=account_id, phone_id=f"telebuba-{account_id}", stage="code"
        )

    monkeypatch.setattr("services.accounts.open_mobile_progress", _progress)
    async with _client(app) as client:
        resp = await client.get("/api/v1/accounts/acc-1/open-mobile/progress")

    assert resp.status_code == 200
    assert resp.json() == {
        "account_id": "acc-1",
        "phone_id": "telebuba-acc-1",
        "stage": "code",
        "reason": None,
    }
