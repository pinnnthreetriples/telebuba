"""``/api/v1/neuroshilling/.../scenario`` — four operations over the scenario service.

Driven against the real service and a temporary database, like the campaign routes
beside it: the thing under test is which refusal becomes which status, and that
mapping only exists where the service's exceptions meet the router.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from sqlalchemy import update as sql_update

from core.config import settings
from core.db import _get_engine
from core.repositories.neuroshilling._tables import _neuroshilling_campaigns
from schemas.neuroshilling_scenario import (
    NeuroshillingRoleInput,
    NeuroshillingScenarioUpdate,
    NeuroshillingStepInput,
)
from services.neuroshilling import _generate, _state

if TYPE_CHECKING:
    from fastapi import FastAPI

_BASE = "/api/v1/neuroshilling/campaigns"


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _create(client: httpx.AsyncClient, *, topic: str = "delivery") -> str:
    response = await client.post(_BASE, json={"name": "Promo"})
    campaign_id = response.json()["campaign_id"]
    saved = await client.put(f"{_BASE}/{campaign_id}", json={"name": "Promo", "topic": topic})
    assert saved.status_code == 200
    return campaign_id


def _dialogue(**overrides: Any) -> dict[str, Any]:
    return {
        "roles": [{"role_id": "a", "name": "Skeptic", "description": "doubts"}],
        "steps": [
            {"role_id": "a", "text": "first"},
            {"role_id": "a", "text": "second", "reply_to_position": 1},
        ],
        **overrides,
    }


async def _set_status(campaign_id: str, status: str) -> None:
    def _write() -> None:
        with _get_engine().begin() as connection:
            connection.execute(
                sql_update(_neuroshilling_campaigns)
                .where(_neuroshilling_campaigns.c.campaign_id == campaign_id)
                .values(status=status),
            )

    await asyncio.to_thread(_write)


@pytest.mark.asyncio
async def test_the_scenario_round_trips_through_put_and_get(app: FastAPI) -> None:
    async with _client(app) as client:
        campaign_id = await _create(client)

        saved = await client.put(f"{_BASE}/{campaign_id}/scenario", json=_dialogue())
        read = await client.get(f"{_BASE}/{campaign_id}/scenario")

    assert saved.status_code == 200
    assert read.status_code == 200
    payload = read.json()
    assert payload["scenario_status"] == "draft"
    assert [step["position"] for step in payload["steps"]] == [1, 2]
    # The client's role key came back as the id the server minted for it.
    assert payload["steps"][0]["role_id"] == payload["roles"][0]["role_id"]


@pytest.mark.asyncio
async def test_approval_is_a_server_verdict_and_editing_undoes_it(app: FastAPI) -> None:
    async with _client(app) as client:
        campaign_id = await _create(client)
        await client.put(f"{_BASE}/{campaign_id}/scenario", json=_dialogue())

        approved = await client.post(f"{_BASE}/{campaign_id}/approve")
        edited = await client.put(f"{_BASE}/{campaign_id}/scenario", json=_dialogue())

    assert approved.status_code == 200
    assert approved.json()["scenario_status"] == "approved"
    assert edited.json()["scenario_status"] == "draft"


@pytest.mark.asyncio
async def test_an_empty_scenario_cannot_be_approved(app: FastAPI) -> None:
    async with _client(app) as client:
        campaign_id = await _create(client)

        response = await client.post(f"{_BASE}/{campaign_id}/approve")

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "scenario_invalid"


@pytest.mark.asyncio
async def test_a_forward_link_is_refused_with_its_stable_code(app: FastAPI) -> None:
    async with _client(app) as client:
        campaign_id = await _create(client)

        response = await client.put(
            f"{_BASE}/{campaign_id}/scenario",
            json=_dialogue(steps=[{"role_id": "a", "text": "only", "reply_to_position": 2}]),
        )

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "scenario_invalid"


@pytest.mark.asyncio
async def test_a_running_campaign_refuses_scenario_writes(app: FastAPI) -> None:
    async with _client(app) as client:
        campaign_id = await _create(client)
        await _set_status(campaign_id, "running")

        saved = await client.put(f"{_BASE}/{campaign_id}/scenario", json=_dialogue())
        approved = await client.post(f"{_BASE}/{campaign_id}/approve")
        generated = await client.post(f"{_BASE}/{campaign_id}/generate", json={})

    assert (saved.status_code, approved.status_code, generated.status_code) == (409, 409, 409)
    assert saved.json()["error"]["message"] == "campaign_running"


@pytest.mark.asyncio
async def test_unknown_campaigns_answer_404_on_every_scenario_route(app: FastAPI) -> None:
    async with _client(app) as client:
        read = await client.get(f"{_BASE}/nope/scenario")
        saved = await client.put(f"{_BASE}/nope/scenario", json=_dialogue())
        approved = await client.post(f"{_BASE}/nope/approve")
        generated = await client.post(f"{_BASE}/nope/generate", json={})

    assert [response.status_code for response in (read, saved, approved, generated)] == [404] * 4


@pytest.mark.asyncio
async def test_generation_answers_with_the_stored_scenario(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(*_args: Any, **_kwargs: Any) -> NeuroshillingScenarioUpdate:
        return NeuroshillingScenarioUpdate(
            roles=[NeuroshillingRoleInput(role_id="1", name="Skeptic")],
            steps=[NeuroshillingStepInput(role_id="1", text="generated")],
        )

    monkeypatch.setattr(_generate, "generate_dialogue", _fake)
    async with _client(app) as client:
        campaign_id = await _create(client)

        response = await client.post(
            f"{_BASE}/{campaign_id}/generate",
            json={"persona_count": 2, "step_count": 4},
        )

    assert response.status_code == 200
    assert [step["text"] for step in response.json()["steps"]] == ["generated"]


@pytest.mark.asyncio
async def test_a_provider_that_answered_nothing_is_a_503(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """503 and not 502: ``api.errors`` has no description for 502 at all."""

    async def _nothing(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(_generate, "generate_dialogue", _nothing)
    async with _client(app) as client:
        campaign_id = await _create(client)

        response = await client.post(f"{_BASE}/{campaign_id}/generate", json={})

    assert response.status_code == 503
    assert response.json()["error"]["message"] == "llm_unavailable"


@pytest.mark.asyncio
async def test_the_daily_budget_answers_409(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.neuroshilling, "max_llm_calls_per_day", 1)
    _state.record_llm_call()
    async with _client(app) as client:
        campaign_id = await _create(client)

        response = await client.post(f"{_BASE}/{campaign_id}/generate", json={})

    assert response.status_code == 409
    assert response.json()["error"]["message"] == "llm_daily_limit_reached"


@pytest.mark.asyncio
async def test_a_topicless_campaign_is_refused_before_the_provider(app: FastAPI) -> None:
    async with _client(app) as client:
        campaign_id = await _create(client, topic="")

        response = await client.post(f"{_BASE}/{campaign_id}/generate", json={})

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "scenario_invalid"


@pytest.mark.asyncio
async def test_an_unbounded_body_is_rejected_by_the_contract(app: FastAPI) -> None:
    """Bounds live at the schema boundary: an unbounded field is an unbounded prompt."""
    async with _client(app) as client:
        campaign_id = await _create(client)

        long_text = await client.put(
            f"{_BASE}/{campaign_id}/scenario",
            json=_dialogue(steps=[{"role_id": "a", "text": "x" * 5000}]),
        )
        too_many = await client.put(
            f"{_BASE}/{campaign_id}/scenario",
            json=_dialogue(steps=[{"role_id": "a", "text": "hi"}] * 60),
        )
        huge_ask = await client.post(
            f"{_BASE}/{campaign_id}/generate",
            json={"persona_count": 99},
        )

    assert (long_text.status_code, too_many.status_code, huge_ask.status_code) == (422, 422, 422)
