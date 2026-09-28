"""Contact-lookup HTTP contract and background dispatch."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from api.deps import get_current_user
from schemas.auth import UserRead
from services.accounts import contact_lookup
from tests.api.accounts_helpers import client as _client

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi import FastAPI


@pytest.fixture(autouse=True)
def _empty_jobs() -> Iterator[None]:
    for store in (
        contact_lookup._jobs,
        contact_lookup._job_owners,
        contact_lookup._pending,
        contact_lookup._cancel_events,
    ):
        store.clear()
    yield
    for store in (
        contact_lookup._jobs,
        contact_lookup._job_owners,
        contact_lookup._pending,
        contact_lookup._cancel_events,
    ):
        store.clear()


@pytest.mark.asyncio
async def test_contact_lookup_routes_start_poll_and_cancel(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatched: list[str] = []

    async def fake_run(job_id: str) -> None:
        dispatched.append(job_id)

    monkeypatch.setattr(contact_lookup, "run_contact_lookup_job", fake_run)
    async with _client(app) as client:
        response = await client.post(
            "/api/v1/accounts/contact-lookup",
            json={
                "account_ids": ["a1"],
                "phones": ["+15551230000", "+15551230000"],
                "min_delay_seconds": 0,
                "max_delay_seconds": 0,
            },
        )
        assert response.status_code == 202
        job = response.json()
        # The duplicate phone collapses to a single lookup unit.
        assert job["total"] == 1
        assert job["status"] == "running"
        assert dispatched == [job["job_id"]]

        active = await client.get("/api/v1/accounts/contact-lookup/active")
        assert active.status_code == 200
        assert active.json() == job

        latest = await client.get("/api/v1/accounts/contact-lookup/latest")
        assert latest.status_code == 200
        assert latest.json() == job

        polled = await client.get(f"/api/v1/accounts/contact-lookup/{job['job_id']}")
        assert polled.status_code == 200
        assert polled.json() == job

        cancelled = await client.post(f"/api/v1/accounts/contact-lookup/{job['job_id']}/cancel")
        assert cancelled.status_code == 200

        missing = await client.get("/api/v1/accounts/contact-lookup/unknown")
        assert missing.status_code == 404


@pytest.mark.asyncio
async def test_contact_lookup_oversized_phone_fails_schema_validation(app: FastAPI) -> None:
    async with _client(app) as client:
        response = await client.post(
            "/api/v1/accounts/contact-lookup",
            json={"account_ids": ["a1"], "phones": ["9" * 41]},
        )
    assert response.status_code == 422
    assert contact_lookup._jobs == {}


@pytest.mark.asyncio
async def test_contact_lookup_only_valid_phones_starts_no_job(app: FastAPI) -> None:
    async with _client(app) as client:
        response = await client.post(
            "/api/v1/accounts/contact-lookup",
            json={"account_ids": ["a1"], "phones": ["garbage", "12"]},
        )
    # Passes request validation but normalises to nothing usable.
    assert response.status_code == 400
    assert contact_lookup._jobs == {}


@pytest.mark.asyncio
async def test_contact_lookup_job_is_private_to_its_owner(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_run(_job_id: str) -> None:
        return None

    monkeypatch.setattr(contact_lookup, "run_contact_lookup_job", fake_run)
    async with _client(app) as client:
        started = await client.post(
            "/api/v1/accounts/contact-lookup",
            json={"account_ids": ["a1"], "phones": ["+15551230000"]},
        )
        assert started.status_code == 202
        job_id = started.json()["job_id"]

        app.dependency_overrides[get_current_user] = lambda: UserRead(
            id="other-admin", username="other", role="admin"
        )
        other_active = await client.get("/api/v1/accounts/contact-lookup/active")
        assert other_active.json() is None
        other_get = await client.get(f"/api/v1/accounts/contact-lookup/{job_id}")
        assert other_get.status_code == 404
        other_cancel = await client.post(f"/api/v1/accounts/contact-lookup/{job_id}/cancel")
        assert other_cancel.status_code == 404
        assert not contact_lookup._cancel_events[job_id].is_set()
