"""Bulk-message HTTP contract and background dispatch."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from api.deps import get_current_user
from core.repositories import bulk_runs as repo
from schemas.auth import UserRead
from schemas.bulk_messages import BulkMessageGenerated, BulkMessageRequest
from schemas.telegram_actions import ActionResult, SendChatMessage
from services.accounts import bulk_messages
from tests.api.accounts_helpers import client as _client

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi import FastAPI


_ALWAYS_OPEN = {
    "work_start": "00:00",
    "work_end": "24:00",
    "work_days": [0, 1, 2, 3, 4, 5, 6],
    "series_size": 0,
}


@pytest.fixture(autouse=True)
def _empty_jobs() -> Iterator[None]:
    bulk_messages._RUNS.clear()
    bulk_messages._TASKS.clear()
    yield
    bulk_messages._RUNS.clear()
    bulk_messages._TASKS.clear()


def _no_launch(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    launched: list[str] = []
    monkeypatch.setattr(bulk_messages, "launch_bulk_message_job", launched.append)
    return launched


@pytest.mark.asyncio
async def test_split_mode_and_pins_reach_the_service(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_launch(monkeypatch)
    async with _client(app) as client:
        response = await client.post(
            "/api/v1/accounts/bulk-messages",
            json={
                "account_ids": ["a1", "a2"],
                "recipients": ["@public_chat", "123456"],
                "text": "Hello",
                "mode": "split",
                "recipient_accounts": {"123456": "a2"},
            },
        )
    assert response.status_code == 202
    job = response.json()
    assert job["total"] == 2
    record = await repo.get_bulk_run(job["job_id"])
    assert record is not None
    data = BulkMessageRequest.model_validate_json(record.request_json)
    assert data.mode == "split"
    assert data.recipient_accounts == {"123456": "a2"}


@pytest.mark.asyncio
async def test_bulk_message_routes_start_poll_and_generate(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatched = _no_launch(monkeypatch)

    async def fake_generate(prompt: str) -> BulkMessageGenerated:
        assert prompt == "Write a greeting"
        return BulkMessageGenerated(text="Hello", provider="gemini")

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

        latest = await client.get("/api/v1/accounts/bulk-messages/latest")
        assert latest.status_code == 200
        assert latest.json() == job

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
    assert await repo.list_live_bulk_runs() == []


@pytest.mark.asyncio
async def test_bulk_message_job_is_private_to_its_owner(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_launch(monkeypatch)
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
        other_latest = await client.get("/api/v1/accounts/bulk-messages/latest")
        assert other_latest.status_code == 200
        assert other_latest.json() is None
        other_get = await client.get(f"/api/v1/accounts/bulk-messages/{job_id}")
        assert other_get.status_code == 404
        other_cancel = await client.post(f"/api/v1/accounts/bulk-messages/{job_id}/cancel")
        assert other_cancel.status_code == 404
    record = await repo.get_bulk_run(job_id)
    assert record is not None
    assert not record.cancel_requested


@pytest.mark.asyncio
async def test_latest_route_returns_completed_job(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_execute(
        account_id: str, action: SendChatMessage, *, domain: str
    ) -> ActionResult:
        assert domain == "bulk_messages"
        return ActionResult(status="ok", action_type=action.action_type, account_id=account_id)

    monkeypatch.setattr(bulk_messages, "execute", fake_execute)
    async with _client(app) as client:
        empty = await client.get("/api/v1/accounts/bulk-messages/latest")
        assert empty.status_code == 200
        assert empty.json() is None

        started = await client.post(
            "/api/v1/accounts/bulk-messages",
            json={
                "account_ids": ["a1"],
                "recipients": ["@public_chat"],
                "text": "Hello",
                "min_delay_seconds": 0,
                "max_delay_seconds": 0,
                "schedule": _ALWAYS_OPEN,
            },
        )
        assert started.status_code == 202
        task = bulk_messages._TASKS.get(started.json()["job_id"])
        if task is not None:
            await task
        latest = await client.get("/api/v1/accounts/bulk-messages/latest")
        assert latest.status_code == 200
        assert latest.json()["job_id"] == started.json()["job_id"]
        assert latest.json()["status"] == "completed"
        assert latest.json()["completed"] == 1
        active = await client.get("/api/v1/accounts/bulk-messages/active")
        assert active.json() is None
