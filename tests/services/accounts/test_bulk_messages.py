"""Bulk-message run validation, pacing, per-pair outcomes and generation."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from core.config import settings
from core.telegram_client._actions import _dispatch_action
from schemas.bulk_messages import BulkMessageJob, BulkMessageRequest
from schemas.gemini import GeminiRequest, GeminiResult
from schemas.telegram_actions import ActionResult, SendChatMessage
from services.accounts import bulk_messages

if TYPE_CHECKING:
    from collections.abc import Coroutine, Iterator


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


def _request(**overrides: object) -> BulkMessageRequest:
    values: dict[str, object] = {
        "account_ids": ["a1", "a2"],
        "recipients": ["@first_chat", "t.me/second_chat"],
        "text": "Hello, world",
        "min_delay_seconds": 2,
        "max_delay_seconds": 2,
    }
    values.update(overrides)
    return BulkMessageRequest.model_validate(values)


def test_invalid_batches_and_recipients_are_rejected_before_start() -> None:
    with pytest.raises(ValidationError):
        _request(max_delay_seconds=1)
    with pytest.raises(ValidationError):
        _request(account_ids=["a1", "a1"])
    with pytest.raises(ValidationError):
        _request(text=" ")
    with pytest.raises(ValueError, match="invalid recipient"):
        bulk_messages.start_bulk_message_job(
            _request(recipients=["t.me/+invitehash123"]), "owner-1"
        )
    with pytest.raises(ValueError, match="duplicate recipients"):
        bulk_messages.start_bulk_message_job(
            _request(recipients=["@first_chat", "t.me/first_chat"]), "owner-1"
        )
    assert bulk_messages._jobs == {}


def test_cancelled_jobs_are_pruned_when_retention_is_full() -> None:
    for index in range(bulk_messages._MAX_RETAINED_JOBS):
        job_id = f"old-{index}"
        bulk_messages._jobs[job_id] = BulkMessageJob(
            job_id=job_id,
            status="cancelled",
            total=1,
            completed=0,
            results=[],
        )
        bulk_messages._job_owners[job_id] = "owner-1"
    bulk_messages.start_bulk_message_job(_request(), "owner-1")
    assert len(bulk_messages._jobs) == bulk_messages._MAX_RETAINED_JOBS
    assert "old-0" not in bulk_messages._jobs
    assert "old-0" not in bulk_messages._job_owners


def test_job_lookup_and_cancel_are_limited_to_its_owner() -> None:
    job = bulk_messages.start_bulk_message_job(_request(), "owner-1")
    assert bulk_messages.get_active_bulk_message_job("owner-1") == job
    assert bulk_messages.get_active_bulk_message_job("owner-2") is None
    assert bulk_messages.get_bulk_message_job(job.job_id, "owner-2") is None
    assert bulk_messages.cancel_bulk_message_job(job.job_id, "owner-2") is None
    assert not bulk_messages._cancel_events[job.job_id].is_set()


@pytest.mark.asyncio
async def test_run_sends_every_pair_in_order_with_delays_and_partial_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str]] = []
    sleeps: list[float] = []

    async def fake_execute(
        account_id: str, action: SendChatMessage, *, domain: str
    ) -> ActionResult:
        assert domain == "bulk_messages"
        calls.append((account_id, action.recipient, action.text))
        refused = account_id == "a1" and action.recipient == "second_chat"
        return ActionResult(
            status="flood_wait" if refused else "ok",
            action_type=action.action_type,
            account_id=account_id,
            flood_wait_seconds=20 if refused else None,
        )

    async def fake_wait_for(
        whatever: Coroutine[object, object, object],
        *,
        timeout: float,  # noqa: ASYNC109
    ) -> None:
        whatever.close()
        sleeps.append(timeout)
        raise TimeoutError

    monkeypatch.setattr(bulk_messages, "execute", fake_execute)
    monkeypatch.setattr(bulk_messages.asyncio, "wait_for", fake_wait_for)

    job = bulk_messages.start_bulk_message_job(_request(), "owner-1")
    assert job.total == 4
    assert job.completed == 0
    with pytest.raises(ValueError, match="bulk_message_run_active"):
        bulk_messages.start_bulk_message_job(_request(), "owner-1")
    await bulk_messages.run_bulk_message_job(job.job_id)

    finished = bulk_messages.get_bulk_message_job(job.job_id, "owner-1")
    assert finished is not None
    assert finished.status == "completed"
    assert finished.completed == 4
    assert [item.status for item in finished.results] == ["ok", "failed", "ok", "ok"]
    assert finished.results[1].error_code == "flood_wait"
    assert calls == [
        ("a1", "first_chat", "Hello, world"),
        ("a1", "second_chat", "Hello, world"),
        ("a2", "first_chat", "Hello, world"),
        ("a2", "second_chat", "Hello, world"),
    ]
    assert sleeps == [2, 2, 2]
    assert bulk_messages._pending == {}


@pytest.mark.asyncio
async def test_unexpected_pair_failure_does_not_abort_run(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_execute(
        account_id: str,
        action: SendChatMessage,
        *,
        domain: str,  # noqa: ARG001
    ) -> ActionResult:
        if action.recipient == "first_chat":
            msg = "connection lost"
            raise RuntimeError(msg)
        return ActionResult(status="ok", action_type=action.action_type, account_id=account_id)

    monkeypatch.setattr(bulk_messages, "execute", fake_execute)
    job = bulk_messages.start_bulk_message_job(
        _request(account_ids=["a1"], min_delay_seconds=0, max_delay_seconds=0), "owner-1"
    )
    await bulk_messages.run_bulk_message_job(job.job_id)
    finished = bulk_messages.get_bulk_message_job(job.job_id, "owner-1")
    assert finished is not None
    assert [item.status for item in finished.results] == ["unconfirmed", "ok"]
    assert [item.error_code for item in finished.results] == ["delivery_unconfirmed", None]


@pytest.mark.asyncio
async def test_unconfirmed_delivery_and_rate_limit_are_not_reported_as_failed_sends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    async def fake_execute(
        account_id: str,
        action: SendChatMessage,
        *,
        domain: str,  # noqa: ARG001
    ) -> ActionResult:
        calls.append((account_id, action.recipient))
        if account_id == "a1" and action.recipient == "first_chat":
            return ActionResult(
                status="unavailable",
                action_type=action.action_type,
                account_id=account_id,
                error_type="UnconfirmedRequest",
            )
        if account_id == "a1" and action.recipient == "second_chat":
            return ActionResult(
                status="flood_wait",
                action_type=action.action_type,
                account_id=account_id,
                flood_wait_seconds=42,
            )
        return ActionResult(status="ok", action_type=action.action_type, account_id=account_id)

    monkeypatch.setattr(bulk_messages, "execute", fake_execute)
    job = bulk_messages.start_bulk_message_job(
        _request(
            recipients=["@first_chat", "@second_chat", "@third_chat"],
            min_delay_seconds=0,
            max_delay_seconds=0,
        ),
        "owner-1",
    )
    await bulk_messages.run_bulk_message_job(job.job_id)
    finished = bulk_messages.get_bulk_message_job(job.job_id, "owner-1")
    assert finished is not None
    assert [item.status for item in finished.results] == [
        "unconfirmed",
        "failed",
        "skipped",
        "ok",
        "ok",
        "ok",
    ]
    assert finished.results[0].error_code == "delivery_unconfirmed"
    assert finished.results[2].retry_after_seconds == 42
    assert ("a1", "third_chat") not in calls


@pytest.mark.asyncio
async def test_cancel_stops_before_next_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    job = bulk_messages.start_bulk_message_job(
        _request(account_ids=["a1"], min_delay_seconds=0, max_delay_seconds=0), "owner-1"
    )

    async def fake_execute(
        account_id: str,
        action: SendChatMessage,
        *,
        domain: str,  # noqa: ARG001
    ) -> ActionResult:
        calls.append(action.recipient)
        bulk_messages.cancel_bulk_message_job(job.job_id, "owner-1")
        return ActionResult(status="ok", action_type=action.action_type, account_id=account_id)

    monkeypatch.setattr(bulk_messages, "execute", fake_execute)
    await bulk_messages.run_bulk_message_job(job.job_id)
    finished = bulk_messages.get_bulk_message_job(job.job_id, "owner-1")
    assert finished is not None
    assert finished.status == "cancelled"
    assert finished.completed == 1
    assert calls == ["first_chat"]
    assert bulk_messages._cancel_events == {}


@pytest.mark.asyncio
async def test_generate_uses_deepseek_and_falls_back_to_gemini(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_deepseek(request: GeminiRequest) -> GeminiResult:
        assert request.api_key == "deepseek-key"
        return GeminiResult(status="ok", text=" DeepSeek draft ")

    async def fake_gemini(request: GeminiRequest) -> GeminiResult:
        assert request.api_key == "gemini-key"
        return GeminiResult(status="ok", text=" Gemini draft ")

    async def fake_secret() -> SimpleNamespace:
        return SimpleNamespace(gemini_api_key="gemini-key", gemini_model="gemini-model")

    monkeypatch.setattr(bulk_messages, "generate_text_deepseek", fake_deepseek)
    monkeypatch.setattr(bulk_messages, "generate_text", fake_gemini)
    monkeypatch.setattr(bulk_messages, "load_warming_settings", fake_secret)
    monkeypatch.setattr(settings.deepseek, "api_key", "deepseek-key")
    generated = await bulk_messages.generate_bulk_message("Write a greeting")
    assert generated.text == "DeepSeek draft"
    assert generated.provider == "deepseek"

    monkeypatch.setattr(settings.deepseek, "api_key", "")
    generated = await bulk_messages.generate_bulk_message("Write a greeting")
    assert generated.text == "Gemini draft"
    assert generated.provider == "gemini"


@pytest.mark.asyncio
async def test_gateway_sends_public_username_or_known_chat_id_as_plain_text() -> None:
    class Client:
        def __init__(self) -> None:
            self.calls: list[tuple[str | int, str]] = []

        async def send_message(self, peer: str | int, text: str) -> SimpleNamespace:
            self.calls.append((peer, text))
            return SimpleNamespace(id=42)

    client = Client()
    await _dispatch_action(client, SendChatMessage(recipient="public_chat", text="*plain*"))  # ty: ignore[invalid-argument-type]
    await _dispatch_action(client, SendChatMessage(recipient="123456", text="hello"))  # ty: ignore[invalid-argument-type]
    assert client.calls == [("public_chat", "*plain*"), (123456, "hello")]
