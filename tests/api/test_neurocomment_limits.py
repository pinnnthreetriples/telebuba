"""The per-account limits endpoints — thin routes over a mocked services.neurocomment.

Kept out of ``test_neurocomment.py`` because that file is already near the 700-line cap
for test sources.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from schemas.neurocomment_limits import (
    AccountLimitGauge,
    AccountLimitsUpdate,
    AccountLimitsView,
)

if TYPE_CHECKING:
    from fastapi import FastAPI


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def _view(account_id: str) -> AccountLimitsView:
    gauge = AccountLimitGauge(limit=20, used=20, fleet_default=20, overridden=False)
    return AccountLimitsView(
        account_id=account_id,
        joins=gauge,
        comments_per_hour=AccountLimitGauge(limit=10, used=1, fleet_default=10),
        comments_per_channel_per_day=AccountLimitGauge(limit=3, used=0, fleet_default=3),
        busiest_channel="@chan",
    )


@pytest.mark.asyncio
async def test_get_limits_returns_the_gauges(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(account_id: str) -> AccountLimitsView:
        return _view(account_id)

    monkeypatch.setattr("services.neurocomment.load_account_limits", _fake)
    async with _client(app) as client:
        resp = await client.get("/api/v1/neurocomment/accounts/acc-1/limits")
    assert resp.status_code == 200
    assert resp.json()["joins"]["used"] == 20
    assert resp.json()["busiest_channel"] == "@chan"


@pytest.mark.asyncio
async def test_get_limits_for_an_unknown_account_is_404(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _none(account_id: str) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr("services.neurocomment.load_account_limits", _none)
    async with _client(app) as client:
        resp = await client.get("/api/v1/neurocomment/accounts/ghost/limits")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_put_limits_passes_the_full_replace_through(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[AccountLimitsUpdate] = []

    async def _fake(account_id: str, data: AccountLimitsUpdate) -> AccountLimitsView:
        seen.append(data)
        return _view(account_id)

    monkeypatch.setattr("services.neurocomment.save_account_limits", _fake)
    async with _client(app) as client:
        resp = await client.put(
            "/api/v1/neurocomment/accounts/acc-1/limits",
            json={
                "max_joins_per_day": 30,
                "max_comments_per_hour": None,
                "max_comments_per_channel_per_day": 0,
            },
        )
    assert resp.status_code == 200
    # A null field reaches the service as "drop this override", and 0 as a real cap —
    # the route must not collapse the two.
    assert seen == [
        AccountLimitsUpdate(
            max_joins_per_day=30,
            max_comments_per_hour=None,
            max_comments_per_channel_per_day=0,
        ),
    ]


@pytest.mark.asyncio
async def test_put_limits_for_an_unknown_account_is_404(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The table has no foreign key, so refusing the write is what keeps orphans out."""

    async def _none(account_id: str, data: AccountLimitsUpdate) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr("services.neurocomment.save_account_limits", _none)
    async with _client(app) as client:
        resp = await client.put(
            "/api/v1/neurocomment/accounts/ghost/limits",
            json={"max_joins_per_day": 5},
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        # Past 64 bits sqlite raises OverflowError, which used to surface as a 500.
        {"max_joins_per_day": 99999999999999999999},
        {"max_joins_per_day": -1},
        {"max_joins_per_day": 1.5},
        # 0 would refuse every comment rather than lift the cap — the gate is a bare ">=".
        {"max_comments_per_hour": 0},
        {"unknown_cap": 5},
    ],
)
async def test_out_of_range_caps_are_refused_as_bad_requests(
    app: FastAPI, body: dict[str, object]
) -> None:
    async with _client(app) as client:
        resp = await client.put("/api/v1/neurocomment/accounts/acc-1/limits", json=body)
    assert resp.status_code == 422
