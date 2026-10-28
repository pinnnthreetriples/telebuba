"""A refusal that sent nothing and says nothing about the account is retried, not a stop."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from schemas.bulk_messages import BulkMessageJob, BulkMessageRequest
from schemas.telegram_actions import ActionResult, SendChatMessage
from services.accounts import bulk_messages
from tests.services.accounts.conftest import ALWAYS_OPEN

if TYPE_CHECKING:
    from tests.services.accounts.conftest import FakeClock

pytestmark = pytest.mark.usefixtures("empty_bulk_jobs")

_DROPPED: dict[str, object] = {"status": "unavailable", "error_type": "ConnectionError"}


def _request(mode: str = "split") -> BulkMessageRequest:
    return BulkMessageRequest.model_validate(
        {
            "account_ids": ["a1", "a2"],
            "recipients": ["@rcpt1", "@rcpt2"],
            "text": "Hello",
            "mode": mode,
            "min_delay_seconds": 0,
            "max_delay_seconds": 0,
            "schedule": ALWAYS_OPEN,
        }
    )


async def _run(
    monkeypatch: pytest.MonkeyPatch,
    request: BulkMessageRequest,
    answers: dict[tuple[str, str], list[dict[str, object]]],
) -> tuple[BulkMessageJob, list[tuple[str, str]]]:
    """``answers`` are consumed per (account, peer) in order; then the send is delivered."""
    calls: list[tuple[str, str]] = []

    async def fake_execute(
        account_id: str,
        action: SendChatMessage,
        *,
        domain: str,  # noqa: ARG001
    ) -> ActionResult:
        calls.append((account_id, action.recipient))
        queue = answers.get((account_id, action.recipient)) or [{"status": "ok"}]
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        return ActionResult.model_validate(
            {"action_type": action.action_type, "account_id": account_id, **answer}
        )

    monkeypatch.setattr(bulk_messages, "execute", fake_execute)
    job = await bulk_messages.start_bulk_message_job(request, "owner-1")
    await bulk_messages.run_bulk_message_job(job.job_id)
    finished = await bulk_messages.get_bulk_message_job(job.job_id, "owner-1")
    assert finished is not None
    return finished, calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "refusal", [_DROPPED, {"status": "slow_mode_wait", "flood_wait_seconds": 30}]
)
async def test_a_send_that_left_nothing_is_retried_by_the_same_account(
    monkeypatch: pytest.MonkeyPatch, bulk_clock: FakeClock, refusal: dict[str, object]
) -> None:
    job, calls = await _run(monkeypatch, _request(), {("a1", "rcpt1"): [refusal, {"status": "ok"}]})

    assert calls == [("a1", "rcpt1"), ("a2", "rcpt2"), ("a1", "rcpt1")]
    assert {(r.account_id, r.recipient, r.status) for r in job.results} == {
        ("a1", "@rcpt1", "ok"),
        ("a2", "@rcpt2", "ok"),
    }
    assert not any(r.handed_over for r in job.results)
    wait = refusal.get("flood_wait_seconds") or settings.bulk_messages.transient_retry_seconds
    assert wait in bulk_clock.sleeps


@pytest.mark.asyncio
@pytest.mark.usefixtures("bulk_clock")
async def test_refusals_like_that_in_a_row_still_take_the_account_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retries = settings.bulk_messages.max_transient_retries

    job, calls = await _run(monkeypatch, _request(), {("a1", "rcpt1"): [_DROPPED]})

    assert calls.count(("a1", "rcpt1")) == retries + 1
    # Then it is a stop like before: split mode hands the recipient on.
    assert ("a2", "rcpt1") in calls
    delivered = {(r.account_id, r.recipient) for r in job.results if r.status == "ok"}
    assert delivered == {("a2", "@rcpt1"), ("a2", "@rcpt2")}
