"""``/neuroshilling/campaigns/{id}/start`` and ``/stop`` — which refusal is which status.

Driven against the real service and a temporary database, because the only thing these
two routes contain is that mapping, and a mocked service would be asserting the mock.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from core.repositories import neuroshilling as repository
from schemas.neuroshilling_scenario import NeuroshillingRoleInput, NeuroshillingStepInput
from schemas.telegram_actions import ResolveChatResult
from services import _account_owner
from services.neuroshilling import _runtime, _seams, _state, _steps, _telegram
from tests.services.neuroshilling.helpers import seed_campaign, sent

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi import FastAPI

    from schemas.telegram_actions import ActionResult, TelegramAction

_BASE = "/api/v1/neuroshilling/campaigns"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Empty the process-global run state and keep every seam off the network."""
    _reset()

    async def _execute(_account_id: str, _action: TelegramAction) -> ActionResult:
        return sent()

    async def _resolve(_account_id: str, _action: TelegramAction) -> ResolveChatResult:
        return ResolveChatResult(chat_id=555, kind="megagroup")

    async def _joins(_campaign_id: str, _account_id: str, _target: str) -> str:
        return "joined"

    async def _nothing(*_args: object) -> None:
        return None

    monkeypatch.setattr(_seams, "execute", _execute)
    monkeypatch.setattr(_seams, "execute_read", _resolve)
    monkeypatch.setattr(_seams, "sleep", _nothing)
    monkeypatch.setattr(_telegram, "join_target", _joins)
    monkeypatch.setattr("services.pacing.await_send_slot", _nothing)
    yield
    _reset()


def _reset() -> None:
    _account_owner.reset_for_tests()
    _state.reset_for_tests()
    _steps.reset_for_tests()
    _runtime.reset_for_tests()


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _stamp(client: httpx.AsyncClient, campaign_id: str) -> str:
    response = await client.get(f"{_BASE}/{campaign_id}/settings")
    assert response.status_code == 200
    return response.json()["campaign"]["updated_at"]


@pytest.mark.asyncio
async def test_start_answers_the_run_status_and_stop_settles_it(app: FastAPI) -> None:
    seeded = await seed_campaign()
    async with _client(app) as client:
        started = await client.post(
            f"{_BASE}/{seeded.campaign_id}/start",
            json={"expected_updated_at": await _stamp(client, seeded.campaign_id)},
        )
        stopped = await client.post(f"{_BASE}/{seeded.campaign_id}/stop")
        board = await client.get(f"{_BASE}/{seeded.campaign_id}/board")

    assert started.status_code == 200
    assert started.json()["status"] == "running"
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "done"
    # The launch card reads progress off the board, not off a second endpoint.
    assert board.json()["run"]["total"] == 2


@pytest.mark.asyncio
async def test_start_refuses_a_newly_approved_scenario_not_seen_in_preview(app: FastAPI) -> None:
    seeded = await seed_campaign()
    async with _client(app) as client:
        preview_stamp = await _stamp(client, seeded.campaign_id)
        await repository.replace_scenario(
            seeded.campaign_id,
            [NeuroshillingRoleInput(role_id=role.role_id, name=role.name) for role in seeded.roles],
            [
                NeuroshillingStepInput(role_id=seeded.roles[0].role_id, text="unseen line"),
                NeuroshillingStepInput(role_id=seeded.roles[1].role_id, text="reply"),
            ],
        )
        changed = await repository.fetch_campaign(seeded.campaign_id)
        assert changed is not None
        assert await repository.approve_scenario(
            seeded.campaign_id, expected_updated_at=changed.updated_at
        )
        refused = await client.post(
            f"{_BASE}/{seeded.campaign_id}/start",
            json={"expected_updated_at": preview_stamp},
        )
        board = await client.get(f"{_BASE}/{seeded.campaign_id}/board")

    assert refused.status_code == 409
    assert refused.json()["error"]["message"] == "campaign_changed"
    assert board.json()["campaign"]["status"] == "idle"
    assert board.json()["run"]["status"] == "idle"


@pytest.mark.asyncio
async def test_start_requires_a_preview_token(app: FastAPI) -> None:
    seeded = await seed_campaign()
    async with _client(app) as client:
        missing = await client.post(f"{_BASE}/{seeded.campaign_id}/start")
        malformed = await client.post(
            f"{_BASE}/{seeded.campaign_id}/start", json={"expected_updated_at": "yesterday"}
        )
    assert missing.status_code == malformed.status_code == 422


@pytest.mark.asyncio
async def test_a_draft_scenario_is_a_conflict(app: FastAPI) -> None:
    seeded = await seed_campaign(approve=False)
    async with _client(app) as client:
        response = await client.post(
            f"{_BASE}/{seeded.campaign_id}/start",
            json={"expected_updated_at": await _stamp(client, seeded.campaign_id)},
        )

    assert response.status_code == 409
    assert response.json()["error"]["message"] == "scenario_not_approved"


@pytest.mark.asyncio
async def test_starting_a_running_campaign_is_a_conflict(app: FastAPI) -> None:
    seeded = await seed_campaign()
    async with _client(app) as client:
        await client.post(
            f"{_BASE}/{seeded.campaign_id}/start",
            json={"expected_updated_at": await _stamp(client, seeded.campaign_id)},
        )
        response = await client.post(
            f"{_BASE}/{seeded.campaign_id}/start",
            json={"expected_updated_at": await _stamp(client, seeded.campaign_id)},
        )
        await client.post(f"{_BASE}/{seeded.campaign_id}/stop")

    assert response.status_code == 409
    assert response.json()["error"]["message"] == "campaign_running"


@pytest.mark.asyncio
async def test_an_unknown_campaign_is_a_404_on_both_routes(app: FastAPI) -> None:
    async with _client(app) as client:
        started = await client.post(
            f"{_BASE}/nope/start",
            json={"expected_updated_at": "2026-09-27T00:00:00+00:00"},
        )
        stopped = await client.post(f"{_BASE}/nope/stop")

    assert (started.status_code, stopped.status_code) == (404, 404)


@pytest.mark.asyncio
async def test_stopping_an_idle_campaign_answers_its_status(app: FastAPI) -> None:
    """Idempotent by choice: the run may have ended a second before the click landed."""
    seeded = await seed_campaign()
    async with _client(app) as client:
        response = await client.post(f"{_BASE}/{seeded.campaign_id}/stop")

    assert response.status_code == 200
    assert response.json()["status"] == "idle"
