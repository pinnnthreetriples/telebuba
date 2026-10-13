"""Bulk messages under the outreach limits: saved pauses and the daily first-message cap."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from core.repositories.outreach import add_outreach_usage, list_outreach_usage, save_outreach_limits
from schemas.bulk_messages import BulkMessageJob, BulkMessageRequest
from schemas.outreach_limits import OutreachDailyUsage, OutreachLimitsUpdate
from schemas.telegram_actions import ActionResult, SendChatMessage
from services.accounts import bulk_messages

pytestmark = pytest.mark.usefixtures("empty_bulk_jobs")


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


async def _run(
    monkeypatch: pytest.MonkeyPatch,
    refused: frozenset[str] = frozenset(),
    **request: object,
) -> tuple[BulkMessageJob, list[tuple[str, str]]]:
    """Run a job with no pause; ``refused`` recipients answer with a privacy refusal."""
    calls: list[tuple[str, str]] = []

    async def fake_execute(
        account_id: str,
        action: SendChatMessage,
        *,
        domain: str,  # noqa: ARG001
    ) -> ActionResult:
        calls.append((account_id, action.recipient))
        refusal = (
            {"status": "failed", "error_type": "UserPrivacyRestrictedError"}
            if action.recipient in refused
            else {"status": "ok"}
        )
        return ActionResult.model_validate(
            {"action_type": action.action_type, "account_id": account_id, **refusal}
        )

    monkeypatch.setattr(bulk_messages, "execute", fake_execute)
    values: dict[str, object] = {
        "text": "Hello",
        "min_delay_seconds": 0,
        "max_delay_seconds": 0,
        **request,
    }
    job = bulk_messages.start_bulk_message_job(BulkMessageRequest.model_validate(values), "owner-1")
    await bulk_messages.run_bulk_message_job(job.job_id)
    finished = bulk_messages.get_bulk_message_job(job.job_id, "owner-1")
    assert finished is not None
    return finished, calls


@pytest.mark.asyncio
async def test_pauses_left_out_of_the_form_come_from_the_saved_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jitter: list[tuple[float, float]] = []

    def fake_uniform(low: float, high: float) -> float:
        jitter.append((low, high))
        return 0

    monkeypatch.setattr(bulk_messages.random, "uniform", fake_uniform)
    await _run(
        monkeypatch,
        account_ids=["a1"],
        recipients=["@rcpt1", "@rcpt2"],
        min_delay_seconds=None,
        max_delay_seconds=None,
    )
    await save_outreach_limits(
        OutreachLimitsUpdate(bulk_min_delay_seconds=90, bulk_max_delay_seconds=600),
    )
    bulk_messages._jobs.clear()
    await _run(
        monkeypatch,
        account_ids=["a1"],
        recipients=["@rcpt1", "@rcpt2"],
        min_delay_seconds=None,
        max_delay_seconds=None,
    )

    # The default 60-180 s, then the operator's saved range.
    assert jitter == [(60, 180), (90, 600)]


def test_a_request_sends_both_pauses_or_neither() -> None:
    with pytest.raises(ValueError, match="both delays"):
        BulkMessageRequest(
            account_ids=["a1"], recipients=["@rcpt1"], text="Hi", max_delay_seconds=9
        )
    request = BulkMessageRequest(
        account_ids=["a1"],
        recipients=["@rcpt1"],
        text="Hi",
        min_delay_seconds=3600,
        max_delay_seconds=3600,
    )
    assert request.max_delay_seconds == 3600


@pytest.mark.asyncio
async def test_each_mode_stops_an_account_at_its_daily_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await save_outreach_limits(OutreachLimitsUpdate(first_messages_per_day=2))
    await add_outreach_usage(OutreachDailyUsage(account_id="a1", day=_today(), first_messages=1))

    job, calls = await _run(
        monkeypatch,
        account_ids=["a1", "a2"],
        recipients=["@rcpt1", "@rcpt2", "@rcpt3"],
        mode="each",
    )

    assert calls == [("a1", "rcpt1"), ("a2", "rcpt1"), ("a2", "rcpt2")]
    by_pair = {(row.account_id, row.recipient): row for row in job.results}
    assert [
        (by_pair["a1", r].status, by_pair["a1", r].error_code) for r in ("@rcpt2", "@rcpt3")
    ] == [
        ("skipped", "daily_limit"),
        ("skipped", "daily_limit"),
    ]
    assert (by_pair["a2", "@rcpt3"].status, by_pair["a2", "@rcpt3"].error_code) == (
        "skipped",
        "daily_limit",
    )
    usage = await list_outreach_usage(["a1", "a2"], _today())
    assert (usage["a1"].first_messages, usage["a2"].first_messages) == (2, 2)


@pytest.mark.asyncio
async def test_a_refused_send_does_not_count_against_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await save_outreach_limits(OutreachLimitsUpdate(first_messages_per_day=1))

    job, calls = await _run(
        monkeypatch,
        frozenset({"rcpt1"}),
        account_ids=["a1"],
        recipients=["@rcpt1", "@rcpt2", "@rcpt3"],
        mode="each",
    )

    assert calls == [("a1", "rcpt1"), ("a1", "rcpt2")]
    assert [row.status for row in job.results] == ["failed", "ok", "skipped"]


@pytest.mark.asyncio
async def test_split_mode_hands_recipients_on_from_a_capped_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await save_outreach_limits(OutreachLimitsUpdate(first_messages_per_day=1))
    # a3 spent its message earlier today, so it never takes a turn.
    await add_outreach_usage(OutreachDailyUsage(account_id="a3", day=_today(), first_messages=1))

    job, calls = await _run(
        monkeypatch,
        account_ids=["a1", "a2", "a3"],
        recipients=["@rcpt1", "@rcpt2", "@rcpt3"],
        mode="split",
    )

    assert calls == [("a1", "rcpt1"), ("a2", "rcpt2")]
    last = job.results[-1]
    assert (last.recipient, last.status, last.error_code) == ("@rcpt3", "skipped", "daily_limit")
    assert job.completed == job.total == 3


@pytest.mark.asyncio
async def test_a_capped_account_leaves_the_rotation_without_skipping_a_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await save_outreach_limits(OutreachLimitsUpdate(first_messages_per_day=2))
    await add_outreach_usage(OutreachDailyUsage(account_id="a1", day=_today(), first_messages=1))

    _job, calls = await _run(
        monkeypatch,
        account_ids=["a1", "a2", "a3"],
        recipients=["@rcpt1", "@rcpt2", "@rcpt3", "@rcpt4", "@rcpt5"],
        mode="split",
    )

    # a1 leaves after r1; the rotation goes on with a2, a3, a2, a3.
    assert calls == [
        ("a1", "rcpt1"),
        ("a2", "rcpt2"),
        ("a3", "rcpt3"),
        ("a2", "rcpt4"),
        ("a3", "rcpt5"),
    ]


@pytest.mark.asyncio
async def test_no_cap_lets_every_send_through(monkeypatch: pytest.MonkeyPatch) -> None:
    await save_outreach_limits(OutreachLimitsUpdate(first_messages_per_day=None))
    await add_outreach_usage(OutreachDailyUsage(account_id="a1", day=_today(), first_messages=99))

    job, _calls = await _run(monkeypatch, account_ids=["a1"], recipients=["@rcpt1", "@rcpt2"])

    assert [row.status for row in job.results] == ["ok", "ok"]
