"""Bulk-message runs under account protection: young caps, rests, auto-stop, typing."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.db import create_account, upsert_spam_status
from core.repositories.outreach_rests import list_outreach_rests, record_outreach_rest
from schemas.accounts import AccountCreate
from schemas.bulk_messages import BulkMessageJob, BulkMessageRequest
from schemas.outreach_protection import OutreachRest, SkippedAccount
from schemas.spam_status import SpamStatusVerdict
from schemas.telegram_actions import ActionResult, SendChatMessage
from schemas.telegram_actions_outreach import OutreachSetTyping
from services.accounts import bulk_messages
from tests.services.accounts.conftest import ALWAYS_OPEN

if TYPE_CHECKING:
    from tests.services.accounts.conftest import FakeClock

pytestmark = pytest.mark.usefixtures("empty_bulk_jobs")

_REAL_SHOW_TYPING = bulk_messages._show_typing
_FLOOD: dict[str, object] = {"status": "flood_wait", "flood_wait_seconds": 30}


async def _run(
    monkeypatch: pytest.MonkeyPatch,
    refusals: dict[tuple[str, str], dict[str, object]] | None = None,
    **request: object,
) -> tuple[BulkMessageJob, list[tuple[str, str]]]:
    """Run a job; ``refusals`` maps (account, peer) to a non-ok result."""
    calls: list[tuple[str, str]] = []

    async def fake_execute(
        account_id: str,
        action: SendChatMessage,
        *,
        domain: str,  # noqa: ARG001
    ) -> ActionResult:
        calls.append((account_id, action.recipient))
        refusal = (refusals or {}).get((account_id, action.recipient), {"status": "ok"})
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


def _rows(job: BulkMessageJob) -> list[tuple[str, str, str, str | None]]:
    return [(r.account_id, r.recipient, r.status, r.error_code) for r in job.results]


@pytest.mark.asyncio
async def test_the_young_cap_holds_within_one_day(
    monkeypatch: pytest.MonkeyPatch, bulk_clock: FakeClock
) -> None:
    await create_account(AccountCreate(account_id="young"))
    days: list[str] = []

    async def fake_execute(
        account_id: str,
        action: SendChatMessage,
        *,
        domain: str,  # noqa: ARG001
    ) -> ActionResult:
        days.append(bulk_clock.now.date().isoformat())
        return ActionResult(status="ok", action_type=action.action_type, account_id=account_id)

    monkeypatch.setattr(bulk_messages, "execute", fake_execute)
    job = await bulk_messages.start_bulk_message_job(
        BulkMessageRequest.model_validate(
            {
                "account_ids": ["young"],
                "recipients": ["@rcpt1", "@rcpt2", "@rcpt3"],
                "text": "Hello",
                "min_delay_seconds": 0,
                "max_delay_seconds": 0,
                "schedule": ALWAYS_OPEN,
                "daily_cap": 5,
            }
        ),
        "owner-1",
    )
    await bulk_messages.run_bulk_message_job(job.job_id)

    # Half of 5 is 2: the third message waits for the next UTC day.
    assert days == ["2026-10-12", "2026-10-12", "2026-10-13"]

    off_job = await bulk_messages.start_bulk_message_job(
        BulkMessageRequest.model_validate(
            {
                "account_ids": ["young"],
                "recipients": ["@rcpt4", "@rcpt5"],
                "text": "Hello",
                "min_delay_seconds": 0,
                "max_delay_seconds": 0,
                "schedule": ALWAYS_OPEN,
                "daily_cap": 5,
                "protection": {"young_half_caps": False},
            }
        ),
        "owner-1",
    )
    days.clear()
    await bulk_messages.run_bulk_message_job(off_job.job_id)
    # The full cap of 5 has room for both on 2026-10-13 (one already spent there).
    assert days == ["2026-10-13", "2026-10-13"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("bulk_clock")
async def test_two_limited_accounts_stop_the_run_and_each_starts_a_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job, calls = await _run(
        monkeypatch,
        {("a1", "rcpt1"): _FLOOD, ("a2", "rcpt1"): {"status": "peer_flood"}},
        account_ids=["a1", "a2", "a3"],
        recipients=["@rcpt1", "@rcpt2"],
        mode="each",
    )

    assert calls == [("a1", "rcpt1"), ("a2", "rcpt1")]
    assert ("a3", "@rcpt1", "skipped", "run_stopped_on_limits") in _rows(job)
    assert ("a3", "@rcpt2", "skipped", "run_stopped_on_limits") in _rows(job)
    assert job.completed == job.total == 6
    rests = await list_outreach_rests(["a1", "a2", "a3"])
    assert {account_id: rest.reason for account_id, rest in rests.items()} == {
        "a1": "flood_wait",
        "a2": "peer_flood",
    }


@pytest.mark.asyncio
@pytest.mark.usefixtures("bulk_clock")
async def test_with_auto_stop_off_the_other_accounts_keep_sending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job, calls = await _run(
        monkeypatch,
        {("a1", "rcpt1"): _FLOOD, ("a2", "rcpt1"): _FLOOD},
        account_ids=["a1", "a2", "a3"],
        recipients=["@rcpt1", "@rcpt2"],
        mode="each",
        protection={"stop_run_on_limits": False},
    )

    assert [call for call in calls if call[0] == "a3"] == [("a3", "rcpt1"), ("a3", "rcpt2")]
    assert not any(r.error_code == "run_stopped_on_limits" for r in job.results)


@pytest.mark.asyncio
@pytest.mark.usefixtures("bulk_clock")
async def test_left_out_accounts_send_nothing_and_split_plans_around_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await create_account(AccountCreate(account_id="spam"))
    await upsert_spam_status(
        SpamStatusVerdict(
            account_id="spam", status="limited", checked_at=datetime.now(UTC).isoformat()
        )
    )
    now = datetime.now(UTC)
    await record_outreach_rest(
        OutreachRest(
            account_id="resting",
            reason="flood_wait",
            limited_at=(now - timedelta(hours=30)).isoformat(),
            rest_until=(now + timedelta(hours=2)).isoformat(),
        )
    )

    split, calls = await _run(
        monkeypatch,
        account_ids=["spam", "resting", "a3"],
        recipients=["@rcpt1", "@rcpt2", "@rcpt3"],
        mode="split",
    )

    assert calls == [("a3", "rcpt1"), ("a3", "rcpt2"), ("a3", "rcpt3")]
    left_out = {s.account_id: s for s in split.skipped_accounts}
    assert {account_id: s.reason for account_id, s in left_out.items()} == {
        "spam": "account_spam_blocked",
        "resting": "account_resting",
    }
    assert (left_out["resting"].retry_after_seconds or 0) > 3600

    each, calls = await _run(
        monkeypatch,
        account_ids=["spam", "a3"],
        recipients=["@rcpt4"],
        mode="each",
    )
    assert calls == [("a3", "rcpt4")]
    assert _rows(each) == [
        ("spam", "@rcpt4", "skipped", "account_spam_blocked"),
        ("a3", "@rcpt4", "ok", None),
    ]
    assert each.skipped_accounts == [
        SkippedAccount(account_id="spam", reason="account_spam_blocked")
    ]

    kept, calls = await _run(
        monkeypatch,
        account_ids=["spam"],
        recipients=["@rcpt5"],
        protection={"skip_injured": False},
    )
    assert calls == [("spam", "rcpt5")]
    assert kept.skipped_accounts == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("bulk_clock")
async def test_typing_shows_before_each_send_and_can_be_switched_off(
    monkeypatch: pytest.MonkeyPatch,
    typing_shows: list[tuple[str, str, float, float]],
) -> None:
    _, calls = await _run(
        monkeypatch, account_ids=["a1"], recipients=["@rcpt1", "@rcpt2"], mode="each"
    )
    assert calls == [("a1", "rcpt1"), ("a1", "rcpt2")]
    assert typing_shows == [("a1", "rcpt1", 2, 5), ("a1", "rcpt2", 2, 5)]

    typing_shows.clear()
    await _run(
        monkeypatch,
        account_ids=["a1"],
        recipients=["@rcpt3"],
        protection={"typing": False},
    )
    assert typing_shows == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("bulk_clock")
async def test_a_failed_typing_never_blocks_or_repeats_the_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bulk_messages, "_show_typing", _REAL_SHOW_TYPING)
    events: list[str] = []

    async def fake_execute(
        account_id: str,
        action: SendChatMessage | OutreachSetTyping,
        *,
        domain: str,  # noqa: ARG001
    ) -> ActionResult:
        events.append(action.action_type)
        if isinstance(action, OutreachSetTyping):
            msg = "typing broke"
            raise TypeError(msg)
        return ActionResult(status="ok", action_type=action.action_type, account_id=account_id)

    monkeypatch.setattr(bulk_messages, "execute", fake_execute)
    job = await bulk_messages.start_bulk_message_job(
        BulkMessageRequest.model_validate(
            {
                "account_ids": ["a1"],
                "recipients": ["@rcpt1"],
                "text": "Hello",
                "min_delay_seconds": 0,
                "max_delay_seconds": 0,
                "schedule": ALWAYS_OPEN,
            }
        ),
        "owner-1",
    )
    await bulk_messages.run_bulk_message_job(job.job_id)
    finished = await bulk_messages.get_bulk_message_job(job.job_id, "owner-1")

    assert events == ["outreach_set_typing", "send_chat_message"]
    assert finished is not None
    assert [r.status for r in finished.results] == ["ok"]


@pytest.mark.asyncio
async def test_show_typing_waits_only_after_telegram_showed_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[OutreachSetTyping] = []
    statuses = iter(["ok", "flood_wait"])

    async def fake_execute(
        account_id: str, action: OutreachSetTyping, *, domain: str
    ) -> ActionResult:
        assert domain == "bulk_messages"
        sent.append(action)
        return ActionResult.model_validate(
            {"status": next(statuses), "action_type": action.action_type, "account_id": account_id}
        )

    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(bulk_messages, "execute", fake_execute)
    monkeypatch.setattr(bulk_messages.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(bulk_messages.random, "uniform", lambda low, high: (low + high) / 2)

    await _REAL_SHOW_TYPING("a1", "123", 2, 4)
    await _REAL_SHOW_TYPING("a1", "123", 2, 4)

    assert [action.recipient for action in sent] == ["123", "123"]
    assert waits == [3]
