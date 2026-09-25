"""Bulk-message HTTP contract and background dispatch."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from api.deps import get_current_user
from schemas.auth import UserRead
from schemas.bulk_messages import BulkMessageGenerated
from services.accounts import bulk_messages
from tests.api.accounts_helpers import client as _client

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi import FastAPI


@pytest.fixture(autouse=True)
def _empty_jobs() -> Iterator[None]:
    bulk_messages._jobs.clear()
    bulk_messages._job_owners.clear()
    bulk_messages._pending.clear()
    bulk_messages._cancel_events.clear()
    yield
    bulk_messages._jobs.clear()
    bulk_messages._job_owners.clear()
    bulk_messages._pending.clear()
    bulk_messages._cancel_events.clear()


@pytest.mark.asyncio
async def test_bulk_message_routes_start_poll_and_generate(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatched: list[str] = []

    async def fake_run(job_id: str) -> None:
        dispatched.append(job_id)

    async def fake_generate(prompt: str) -> BulkMessageGenerated:
        assert prompt == "Write a greeting"
        return BulkMessageGenerated(text="Hello", provider="gemini")

    monkeypatch.setattr(bulk_messages, "run_bulk_message_job", fake_run)
    monkeypatch.setattr(bulk_messages, "generate_bulk_message", fake_generate)
    async with _client(app) as client:
        response = await client.post(
            "/api/v1/accounts/bulk-messages",
            json={
                "account_ids": ["a1"],
                "recipients": ["@public_chat"],
                "text": "Hello",
                "min_delay_seconds": 0,
                "max_delay_seconds": 0,
            },
        )
        assert response.status_code == 202
        job = response.json()
        assert job["total"] == 1
        assert job["status"] == "running"
        assert dispatched == [job["job_id"]]

        active = await client.get("/api/v1/accounts/bulk-messages/active")
        assert active.status_code == 200
        assert active.json() == job

        polled = await client.get(f"/api/v1/accounts/bulk-messages/{job['job_id']}")
        assert polled.status_code == 200
        assert polled.json() == job

        cancelled = await client.post(f"/api/v1/accounts/bulk-messages/{job['job_id']}/cancel")
        assert cancelled.status_code == 200

        generated = await client.post(
            "/api/v1/accounts/bulk-messages/generate",
            json={"prompt": "Write a greeting"},
        )
        assert generated.status_code == 200
        assert generated.json() == {"text": "Hello", "provider": "gemini"}

        missing = await client.get("/api/v1/accounts/bulk-messages/unknown")
        assert missing.status_code == 404


@pytest.mark.asyncio
async def test_bulk_message_invalid_recipient_is_rejected_without_job(app: FastAPI) -> None:
    async with _client(app) as client:
        response = await client.post(
            "/api/v1/accounts/bulk-messages",
            json={"account_ids": ["a1"], "recipients": ["t.me/+invitehash123"], "text": "Hello"},
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"
    assert bulk_messages._jobs == {}


@pytest.mark.asyncio
async def test_bulk_message_job_is_private_to_its_owner(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_run(_job_id: str) -> None:
        return None

    monkeypatch.setattr(bulk_messages, "run_bulk_message_job", fake_run)
    async with _client(app) as client:
        started = await client.post(
            "/api/v1/accounts/bulk-messages",
            json={"account_ids": ["a1"], "recipients": ["@public_chat"], "text": "Hello"},
        )
        assert started.status_code == 202
        job_id = started.json()["job_id"]

        app.dependency_overrides[get_current_user] = lambda: UserRead(
            id="other-admin", username="other", role="admin"
        )
        other_active = await client.get("/api/v1/accounts/bulk-messages/active")
        assert other_active.status_code == 200
        assert other_active.json() is None
        other_get = await client.get(f"/api/v1/accounts/bulk-messages/{job_id}")
        assert other_get.status_code == 404
        other_cancel = await client.post(f"/api/v1/accounts/bulk-messages/{job_id}/cancel")
        assert other_cancel.status_code == 404
        assert not bulk_messages._cancel_events[job_id].is_set()
