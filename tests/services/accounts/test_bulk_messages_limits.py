"""Bulk messages under the outreach limits: saved pauses and the daily first-message cap.

A run that meets an account's daily cap waits for the next UTC day instead of
dropping recipients; split mode passes them to an account with room first.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.repositories.outreach import add_outreach_usage, list_outreach_usage, save_outreach_limits
from schemas.bulk_messages import BulkMessageJob, BulkMessageRequest
from schemas.outreach_limits import OutreachDailyUsage, OutreachLimitsUpdate
from schemas.telegram_actions import ActionResult, SendChatMessage
from services.accounts import _bulk_runner, bulk_messages
from tests.services.accounts.conftest import ALWAYS_OPEN

if TYPE_CHECKING:
    from tests.services.accounts.conftest import FakeClock

pytestmark = pytest.mark.usefixtures("empty_bulk_jobs")

_DAY_1 = "2026-10-12"
_DAY_2 = "2026-10-13"


async def _run(
    monkeypatch: pytest.MonkeyPatch,
    clock: FakeClock,
    refused: frozenset[str] = frozenset(),
    **request: object,
) -> tuple[BulkMessageJob, list[tuple[str, str, str]]]:
    """Run a job; each call is recorded with the UTC day it went out on."""
    calls: list[tuple[str, str, str]] = []

    async def fake_execute(
        account_id: str,
        action: SendChatMessage,
        *,
        domain: str,  # noqa: ARG001
    ) -> ActionResult:
        calls.append((clock.now.date().isoformat(), account_id, action.recipient))
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
        "schedule": ALWAYS_OPEN,
        **request,
    }
    job = await bulk_messages.start_bulk_message_job(
        BulkMessageRequest.model_validate(values), "owner-1"
    )
    await bulk_messages.run_bulk_message_job(job.job_id)
    finished = await bulk_messages.get_bulk_message_job(job.job_id, "owner-1")
    assert finished is not None
    return finished, calls


@pytest.mark.asyncio
async def test_pauses_left_out_of_the_form_come_from_the_saved_limits(
    monkeypatch: pytest.MonkeyPatch,
    bulk_clock: FakeClock,
) -> None:
    jitter: list[tuple[float, float]] = []

    def fake_uniform(low: float, high: float) -> float:
        jitter.append((low, high))
        return 0

    monkeypatch.setattr(_bulk_runner.random, "uniform", fake_uniform)
    await _run(
        monkeypatch,
        bulk_clock,
        account_ids=["a1"],
        recipients=["@rcpt1"],
        min_delay_seconds=None,
        max_delay_seconds=None,
    )
    await save_outreach_limits(
        OutreachLimitsUpdate(bulk_min_delay_seconds=90, bulk_max_delay_seconds=600),
    )
    await _run(
        monkeypatch,
        bulk_clock,
        account_ids=["a1"],
        recipients=["@rcpt1"],
        min_delay_seconds=None,
        max_delay_seconds=None,
    )

    # The default 60-180 s, then the operator's saved range.
    assert jitter == [(60, 180), (90, 600)]


def test_a_request_sends_both_pauses_or_neither() -> None:
    with pytest.raises(ValueError, match="both delays"):
        BulkMessageRequest(account_ids=["a1"], recipients=["@r1"], text="Hi", max_delay_seconds=9)
    request = BulkMessageRequest(
        account_ids=["a1"],
        recipients=["@r1"],
        text="Hi",
        min_delay_seconds=3600,
        max_delay_seconds=3600,
    )
    assert request.max_delay_seconds == 3600


@pytest.mark.asyncio
async def test_each_mode_waits_for_the_next_day_at_an_accounts_cap(
    monkeypatch: pytest.MonkeyPatch,
    bulk_clock: FakeClock,
) -> None:
    await save_outreach_limits(OutreachLimitsUpdate(first_messages_per_day=2))
    await add_outreach_usage(OutreachDailyUsage(account_id="a1", day=_DAY_1, first_messages=1))

    job, calls = await _run(
        monkeypatch,
        bulk_clock,
        account_ids=["a1", "a2"],
        recipients=["@rcpt1", "@rcpt2", "@rcpt3"],
        mode="each",
    )

    assert sorted(calls) == [
        (_DAY_1, "a1", "rcpt1"),
        (_DAY_1, "a2", "rcpt1"),
        (_DAY_1, "a2", "rcpt2"),
        (_DAY_2, "a1", "rcpt2"),
        (_DAY_2, "a1", "rcpt3"),
        (_DAY_2, "a2", "rcpt3"),
    ]
    assert {r.status for r in job.results} == {"ok"}
    first = await list_outreach_usage(["a1", "a2"], _DAY_1)
    second = await list_outreach_usage(["a1", "a2"], _DAY_2)
    assert (first["a1"].first_messages, first["a2"].first_messages) == (2, 2)
    assert (second["a1"].first_messages, second["a2"].first_messages) == (2, 1)


@pytest.mark.asyncio
async def test_a_refused_send_does_not_count_against_the_cap(
    monkeypatch: pytest.MonkeyPatch,
    bulk_clock: FakeClock,
) -> None:
    await save_outreach_limits(OutreachLimitsUpdate(first_messages_per_day=1))

    job, calls = await _run(
        monkeypatch,
        bulk_clock,
        frozenset({"rcpt1"}),
        account_ids=["a1"],
        recipients=["@rcpt1", "@rcpt2", "@rcpt3"],
        mode="each",
    )

    assert calls == [(_DAY_1, "a1", "rcpt1"), (_DAY_1, "a1", "rcpt2"), (_DAY_2, "a1", "rcpt3")]
    assert [row.status for row in job.results] == ["failed", "ok", "ok"]


@pytest.mark.asyncio
async def test_split_mode_passes_a_capped_accounts_recipients_to_one_with_room(
    monkeypatch: pytest.MonkeyPatch,
    bulk_clock: FakeClock,
) -> None:
    await save_outreach_limits(OutreachLimitsUpdate(first_messages_per_day=2))
    # a2 spent its messages earlier today; a1 still has two.
    await add_outreach_usage(OutreachDailyUsage(account_id="a2", day=_DAY_1, first_messages=2))

    job, calls = await _run(
        monkeypatch,
        bulk_clock,
        account_ids=["a1", "a2"],
        recipients=["@rcpt1", "@rcpt2", "@rcpt3"],
        mode="split",
    )

    assert calls == [
        (_DAY_1, "a1", "rcpt1"),
        (_DAY_1, "a1", "rcpt2"),
        # Both out of today's room: the last one waits for the next UTC day.
        (_DAY_2, "a1", "rcpt3"),
    ]
    assert job.completed == job.total == 3


@pytest.mark.asyncio
async def test_a_pinned_recipient_waits_for_its_own_account(
    monkeypatch: pytest.MonkeyPatch,
    bulk_clock: FakeClock,
) -> None:
    await save_outreach_limits(OutreachLimitsUpdate(first_messages_per_day=1))
    await add_outreach_usage(OutreachDailyUsage(account_id="a2", day=_DAY_1, first_messages=1))

    _job, calls = await _run(
        monkeypatch,
        bulk_clock,
        account_ids=["a1", "a2"],
        recipients=["@rcpt1", "222"],
        recipient_accounts={"222": "a2"},
        mode="split",
    )

    assert calls == [(_DAY_1, "a1", "rcpt1"), (_DAY_2, "a2", "222")]


@pytest.mark.asyncio
async def test_no_cap_lets_every_send_through(
    monkeypatch: pytest.MonkeyPatch,
    bulk_clock: FakeClock,
) -> None:
    await save_outreach_limits(OutreachLimitsUpdate(first_messages_per_day=None))
    await add_outreach_usage(OutreachDailyUsage(account_id="a1", day=_DAY_1, first_messages=99))

    job, calls = await _run(
        monkeypatch, bulk_clock, account_ids=["a1"], recipients=["@rcpt1", "@rcpt2"]
    )

    assert [row.status for row in job.results] == ["ok", "ok"]
    assert {day for day, _a, _r in calls} == {_DAY_1}


@pytest.mark.asyncio
async def test_a_run_cap_applies_even_when_the_saved_cap_is_off(
    monkeypatch: pytest.MonkeyPatch,
    bulk_clock: FakeClock,
) -> None:
    await save_outreach_limits(OutreachLimitsUpdate(first_messages_per_day=None))

    _job, calls = await _run(
        monkeypatch,
        bulk_clock,
        account_ids=["a1"],
        recipients=["@rcpt1", "@rcpt2"],
        daily_cap=1,
    )

    assert calls == [(_DAY_1, "a1", "rcpt1"), (_DAY_2, "a1", "rcpt2")]
