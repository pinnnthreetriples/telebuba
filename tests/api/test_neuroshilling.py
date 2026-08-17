"""``/api/v1/neuroshilling`` routes — five operations over the campaign service.

Driven against the real service and a temporary database rather than a mocked
one: the whole point of these routes is which refusal becomes which status, and
that mapping only exists where the service's exceptions meet the router.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from sqlalchemy import update as sql_update

from core.db import _get_engine, create_account
from core.repositories.neuroshilling._tables import _neuroshilling_campaigns
from schemas.accounts import AccountCreate
from services import _account_owner

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi import FastAPI

_BASE = "/api/v1/neuroshilling/campaigns"


@pytest.fixture(autouse=True)
def _reset_process_state() -> Iterator[None]:
    """The pacer is reset suite-wide by the root conftest; this is the registry."""
    _account_owner.reset_for_tests()
    yield
    _account_owner.reset_for_tests()


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def _form(**overrides: Any) -> dict[str, Any]:
    return {"name": "Promo", **overrides}


async def _create(client: httpx.AsyncClient, name: str = "Promo") -> str:
    response = await client.post(_BASE, json={"name": name})
    assert response.status_code == 200
    return response.json()["campaign_id"]


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
async def test_campaigns_are_created_and_listed(app: FastAPI) -> None:
    async with _client(app) as client:
        campaign_id = await _create(client)

        listed = await client.get(_BASE)

    assert listed.status_code == 200
    body = listed.json()["campaigns"]
    assert [item["campaign_id"] for item in body] == [campaign_id]
    assert body[0]["status"] == "idle"
    assert body[0]["scenario_status"] == "draft"


@pytest.mark.asyncio
async def test_the_form_round_trips_through_update_and_the_board(app: FastAPI) -> None:
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    async with _client(app) as client:
        campaign_id = await _create(client)

        saved = await client.put(
            f"{_BASE}/{campaign_id}",
            json=_form(
                topic="delivery",
                targets_raw="@news https://t.me/sport",
                messages_per_hour=5,
                accounts=[{"account_id": "acc-1", "is_reserve": True}],
            ),
        )
        board = await client.get(f"{_BASE}/{campaign_id}/board")

    assert saved.status_code == 200
    assert saved.json()["messages_per_hour"] == 5
    assert board.status_code == 200
    payload = board.json()
    assert payload["targets"] == ["news", "sport"]
    # One list, with the roster overlaid on it — no second list to join by id.
    assigned = [item for item in payload["available"] if item["assigned"]]
    assert [item["account_id"] for item in assigned] == ["acc-1"]
    assert assigned[0]["is_reserve"] is True
    assert payload["available"][0]["busy_owner"] is None
    assert (payload["campaign"]["status"], payload["campaign"]["run_id"]) == ("idle", None)


@pytest.mark.asyncio
async def test_parallel_run_mode_is_refused_with_its_stable_code(app: FastAPI) -> None:
    async with _client(app) as client:
        campaign_id = await _create(client)

        response = await client.put(f"{_BASE}/{campaign_id}", json=_form(run_mode="parallel"))

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "run_mode_not_supported"


@pytest.mark.asyncio
async def test_a_reversed_pause_range_is_rejected_by_the_contract(app: FastAPI) -> None:
    """``min <= max`` is OUR rule, not pydantic's, and this is its only test."""
    async with _client(app) as client:
        campaign_id = await _create(client)

        response = await client.put(
            f"{_BASE}/{campaign_id}",
            json=_form(pause_min_seconds=40, pause_max_seconds=10),
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_a_running_campaign_refuses_edits_and_deletion(app: FastAPI) -> None:
    async with _client(app) as client:
        campaign_id = await _create(client)
        await _set_status(campaign_id, "running")

        edit = await client.put(f"{_BASE}/{campaign_id}", json=_form())
        removal = await client.delete(f"{_BASE}/{campaign_id}")

    assert (edit.status_code, removal.status_code) == (409, 409)
    assert edit.json()["error"]["message"] == "campaign_running"
    assert removal.json()["error"]["message"] == "campaign_running"


@pytest.mark.asyncio
async def test_unknown_campaigns_answer_404_on_every_route(app: FastAPI) -> None:
    async with _client(app) as client:
        board = await client.get(f"{_BASE}/nope/board")
        edit = await client.put(f"{_BASE}/nope", json=_form())
        removal = await client.delete(f"{_BASE}/nope")

    assert (board.status_code, edit.status_code, removal.status_code) == (404, 404, 404)


@pytest.mark.asyncio
async def test_delete_answers_204_and_removes_the_campaign(app: FastAPI) -> None:
    async with _client(app) as client:
        campaign_id = await _create(client)

        removal = await client.delete(f"{_BASE}/{campaign_id}")
        listed = await client.get(_BASE)

    assert removal.status_code == 204
    assert listed.json()["campaigns"] == []


def test_the_operation_ids_the_generated_client_is_named_after(app: FastAPI) -> None:
    """These names are copied verbatim into the TypeScript client and are forever.

    Renaming one after the first ``tools.gen_api`` run rewrites every call site,
    so they are pinned here rather than left to a reviewer's eye.
    """
    paths = app.openapi()["paths"]
    assert {
        f"{method} {path.rsplit('neuroshilling', 1)[1]}": spec["operationId"]
        for path, methods in paths.items()
        if path.startswith("/api/v1/neuroshilling")
        for method, spec in methods.items()
    } == {
        "get /campaigns": "listNeuroshillingCampaigns",
        "post /campaigns": "createNeuroshillingCampaign",
        "put /campaigns/{campaign_id}": "updateNeuroshillingCampaign",
        "delete /campaigns/{campaign_id}": "deleteNeuroshillingCampaign",
        "get /campaigns/{campaign_id}/board": "getNeuroshillingBoard",
        "get /campaigns/{campaign_id}/scenario": "getNeuroshillingScenario",
        "put /campaigns/{campaign_id}/scenario": "setNeuroshillingScenario",
        "post /campaigns/{campaign_id}/generate": "generateNeuroshillingScenario",
        "post /campaigns/{campaign_id}/approve": "approveNeuroshillingScenario",
    }
