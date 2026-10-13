"""Bulk-message run validation, pacing, per-pair outcomes and generation."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from core.repositories import bulk_runs as repo
from core.telegram_client._actions import _dispatch_action
from schemas.bulk_messages import BulkMessageRequest
from schemas.gemini import GeminiRequest, GeminiResult
from schemas.telegram_actions import ActionResult, SendChatMessage
from services.accounts import bulk_messages
from tests.services.accounts.conftest import ALWAYS_OPEN

if TYPE_CHECKING:
    from tests.services.accounts.conftest import FakeClock

pytestmark = pytest.mark.usefixtures("empty_bulk_jobs", "bulk_clock")


def _request(**overrides: object) -> BulkMessageRequest:
    values: dict[str, object] = {
        "account_ids": ["a1", "a2"],
        "recipients": ["@first_chat", "t.me/second_chat"],
        "text": "Hello, world",
        "min_delay_seconds": 0,
        "max_delay_seconds": 0,
        "schedule": ALWAYS_OPEN,
    }
    values.update(overrides)
    return BulkMessageRequest.model_validate(values)


def _ok(account_id: str, action: SendChatMessage) -> ActionResult:
    return ActionResult(status="ok", action_type=action.action_type, account_id=account_id)


async def _finish(job_id: str) -> None:
    await bulk_messages.run_bulk_message_job(job_id)


@pytest.mark.asyncio
async def test_invalid_batches_and_recipients_are_rejected_before_start() -> None:
    with pytest.raises(ValidationError):
        _request(max_delay_seconds=1, min_delay_seconds=2)
    with pytest.raises(ValidationError):
        _request(account_ids=["a1", "a1"])
    with pytest.raises(ValidationError):
        _request(text=" ")
    with pytest.raises(ValidationError):
        _request(recipient_phones={"@stranger": "+15550000000"})
    with pytest.raises(ValueError, match="invalid recipient"):
        await bulk_messages.start_bulk_message_job(
            _request(recipients=["t.me/+invitehash123"]), "owner-1"
        )
    with pytest.raises(ValueError, match="duplicate recipients"):
        await bulk_messages.start_bulk_message_job(
            _request(recipients=["@first_chat", "t.me/first_chat"]), "owner-1"
        )
    assert await repo.list_live_bulk_runs() == []


@pytest.mark.asyncio
async def test_finished_runs_are_pruned_when_retention_is_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bulk_messages, "execute", _execute_ok)
    first = await bulk_messages.start_bulk_message_job(_request(account_ids=["a1"]), "owner-1")
    await _finish(first.job_id)
    # The 21st start keeps the 20 newest finished runs besides itself; the first goes.
    for _ in range(bulk_messages._MAX_RETAINED_JOBS + 1):
        job = await bulk_messages.start_bulk_message_job(_request(account_ids=["a1"]), "owner-1")
        await _finish(job.job_id)

    assert await repo.get_bulk_run(first.job_id) is None
    assert await repo.list_bulk_run_items(first.job_id) == []


async def _execute_ok(
    account_id: str,
    action: SendChatMessage,
    *,
    domain: str,  # noqa: ARG001
) -> ActionResult:
    return _ok(account_id, action)


@pytest.mark.asyncio
async def test_job_lookup_and_cancel_are_limited_to_its_owner() -> None:
    job = await bulk_messages.start_bulk_message_job(_request(), "owner-1")
    assert await bulk_messages.get_active_bulk_message_job("owner-1") == job
    assert await bulk_messages.get_active_bulk_message_job("owner-2") is None
    assert await bulk_messages.get_latest_bulk_message_job("owner-1") == job
    assert await bulk_messages.get_latest_bulk_message_job("owner-2") is None
    assert await bulk_messages.get_bulk_message_job(job.job_id, "owner-2") is None
    assert await bulk_messages.cancel_bulk_message_job(job.job_id, "owner-2") is None
    record = await repo.get_bulk_run(job.job_id)
    assert record is not None
    assert not record.cancel_requested


@pytest.mark.asyncio
async def test_latest_job_includes_a_finished_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bulk_messages, "execute", _execute_ok)
    first = await bulk_messages.start_bulk_message_job(_request(), "owner-1")
    await _finish(first.job_id)

    assert await bulk_messages.get_active_bulk_message_job("owner-1") is None
    latest = await bulk_messages.get_latest_bulk_message_job("owner-1")
    assert latest is not None
    assert (latest.job_id, latest.status) == (first.job_id, "completed")


@pytest.mark.asyncio
async def test_run_sends_every_pair_with_each_accounts_own_pause(
    monkeypatch: pytest.MonkeyPatch,
    bulk_clock: FakeClock,
) -> None:
    calls: list[tuple[str, str, str]] = []

    async def fake_execute(
        account_id: str, action: SendChatMessage, *, domain: str
    ) -> ActionResult:
        assert domain == "bulk_messages"
        calls.append((account_id, action.recipient, action.text))
        refused = account_id == "a1" and action.recipient == "second_chat"
        return ActionResult(
            status="failed" if refused else "ok",
            action_type=action.action_type,
            account_id=account_id,
            error_type="UserPrivacyRestrictedError" if refused else None,
        )

    monkeypatch.setattr(bulk_messages, "execute", fake_execute)
    job = await bulk_messages.start_bulk_message_job(
        _request(min_delay_seconds=2, max_delay_seconds=2), "owner-1"
    )
    assert (job.total, job.completed) == (4, 0)
    with pytest.raises(ValueError, match="bulk_message_run_active"):
        await bulk_messages.start_bulk_message_job(_request(), "owner-1")
    await _finish(job.job_id)

    finished = await bulk_messages.get_bulk_message_job(job.job_id, "owner-1")
    assert finished is not None
    assert (finished.status, finished.completed) == ("completed", 4)
    # a2 does not wait for a1's pause: the accounts take turns as each one is ready.
    assert calls == [
        ("a1", "first_chat", "Hello, world"),
        ("a2", "first_chat", "Hello, world"),
        ("a1", "second_chat", "Hello, world"),
        ("a2", "second_chat", "Hello, world"),
    ]
    assert [item.status for item in finished.results] == ["ok", "ok", "failed", "ok"]
    assert [s for s in bulk_clock.sleeps if s] == [2]


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
        return _ok(account_id, action)

    monkeypatch.setattr(bulk_messages, "execute", fake_execute)
    job = await bulk_messages.start_bulk_message_job(_request(account_ids=["a1"]), "owner-1")
    await _finish(job.job_id)
    finished = await bulk_messages.get_bulk_message_job(job.job_id, "owner-1")
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
        return _ok(account_id, action)

    monkeypatch.setattr(bulk_messages, "execute", fake_execute)
    job = await bulk_messages.start_bulk_message_job(
        _request(recipients=["@first_chat", "@second_chat", "@third_chat"]), "owner-1"
    )
    await _finish(job.job_id)
    finished = await bulk_messages.get_bulk_message_job(job.job_id, "owner-1")
    assert finished is not None
    by_pair = {(r.account_id, r.recipient): r for r in finished.results}
    assert by_pair["a1", "@first_chat"].status == "unconfirmed"
    assert by_pair["a1", "@first_chat"].error_code == "delivery_unconfirmed"
    assert by_pair["a1", "@second_chat"].status == "failed"
    assert by_pair["a1", "@third_chat"].status == "skipped"
    assert by_pair["a1", "@third_chat"].retry_after_seconds == 42
    assert {by_pair["a2", r].status for r in ("@first_chat", "@second_chat", "@third_chat")} == {
        "ok"
    }
    assert ("a1", "third_chat") not in calls


@pytest.mark.asyncio
async def test_a_dead_account_stops_instead_of_failing_every_pair(
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
        if account_id == "a1":
            return ActionResult(
                status="failed",
                action_type=action.action_type,
                account_id=account_id,
                error_type="ProfileGatewayError",
                error_message="session_dead",
            )
        return _ok(account_id, action)

    monkeypatch.setattr(bulk_messages, "execute", fake_execute)
    job = await bulk_messages.start_bulk_message_job(_request(), "owner-1")
    await _finish(job.job_id)
    finished = await bulk_messages.get_bulk_message_job(job.job_id, "owner-1")
    assert finished is not None
    assert [(r.account_id, r.status, r.error_code) for r in finished.results] == [
        ("a1", "failed", "session_dead"),
        ("a1", "skipped", "session_dead"),
        ("a2", "ok", None),
        ("a2", "ok", None),
    ]
    assert calls == [("a1", "first_chat"), ("a2", "first_chat"), ("a2", "second_chat")]


@pytest.mark.asyncio
async def test_cancel_stops_before_next_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    job = await bulk_messages.start_bulk_message_job(_request(account_ids=["a1"]), "owner-1")

    async def fake_execute(
        account_id: str,
        action: SendChatMessage,
        *,
        domain: str,  # noqa: ARG001
    ) -> ActionResult:
        calls.append(action.recipient)
        await bulk_messages.cancel_bulk_message_job(job.job_id, "owner-1")
        return _ok(account_id, action)

    monkeypatch.setattr(bulk_messages, "execute", fake_execute)
    await _finish(job.job_id)
    finished = await bulk_messages.get_bulk_message_job(job.job_id, "owner-1")
    assert finished is not None
    assert (finished.status, finished.completed, finished.total) == ("cancelled", 1, 2)
    assert calls == ["first_chat"]
    assert bulk_messages._RUNS == {}


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

    deepseek_key = "deepseek-key"

    async def fake_secret() -> SimpleNamespace:
        return SimpleNamespace(
            gemini_api_key="gemini-key",
            gemini_model="gemini-model",
            deepseek_api_key=deepseek_key,
            text_llm_provider="deepseek",
            gemini_max_retries=1,
            gemini_min_interval_seconds=0.0,
        )

    monkeypatch.setattr(bulk_messages, "generate_text_deepseek", fake_deepseek)
    monkeypatch.setattr(bulk_messages, "generate_text", fake_gemini)
    monkeypatch.setattr(bulk_messages, "load_warming_settings", fake_secret)
    generated = await bulk_messages.generate_bulk_message("Write a greeting")
    assert generated.text == "DeepSeek draft"
    assert generated.provider == "deepseek"

    deepseek_key = ""
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
