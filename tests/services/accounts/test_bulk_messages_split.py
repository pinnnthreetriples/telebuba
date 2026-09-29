"""Split-mode bulk messages: one message per recipient, round-robin with hand-over."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from schemas.bulk_messages import BulkMessageJob, BulkMessageRequest
from schemas.telegram_actions import ActionResult, SendChatMessage
from services.accounts import bulk_messages

pytestmark = pytest.mark.usefixtures("empty_bulk_jobs")

_ACCOUNTS = ["a1", "a2", "a3", "a4"]
_RECIPIENTS = ["@rcpt1", "@rcpt2", "@rcpt3", "@rcpt4", "@rcpt5"]


def _request(**overrides: object) -> BulkMessageRequest:
    values: dict[str, object] = {
        "account_ids": _ACCOUNTS,
        "recipients": _RECIPIENTS,
        "text": "Hello",
        "mode": "split",
    }
    values.update(overrides)
    return BulkMessageRequest.model_validate(values)


async def _run(
    monkeypatch: pytest.MonkeyPatch,
    request: BulkMessageRequest,
    refusals: dict[tuple[str, str], dict[str, object]] | None = None,
) -> tuple[BulkMessageJob, list[tuple[str, str]]]:
    """Run a split job; ``refusals`` maps (account, peer) to a non-ok result."""
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
    job = bulk_messages.start_bulk_message_job(request, "owner-1")
    await bulk_messages.run_bulk_message_job(job.job_id)
    finished = bulk_messages.get_bulk_message_job(job.job_id, "owner-1")
    assert finished is not None
    return finished, calls


def _pairs(job: BulkMessageJob) -> list[tuple[str, str, str, bool]]:
    return [(r.account_id, r.recipient, r.status, r.handed_over) for r in job.results]


def test_split_limits_differ_from_each_mode() -> None:
    many = [f"@user{index}" for index in range(500)]
    assert len(_request(recipients=many).recipients) == 500
    assert _request().mode == "split"
    assert BulkMessageRequest(account_ids=["a1"], recipients=["@rcpt1"], text="x").mode == "each"
    with pytest.raises(ValidationError):
        _request(recipients=[*many, "@one_more"])
    with pytest.raises(ValidationError, match="at most 50 recipients"):
        _request(mode="each", account_ids=["a1"], recipients=many[:51])
    with pytest.raises(ValidationError, match="500 sends"):
        _request(mode="each", account_ids=[f"a{i}" for i in range(11)], recipients=many[:50])


def test_pins_must_name_listed_recipients_and_accounts() -> None:
    assert _request(recipient_accounts={"@rcpt1": "a2"}).recipient_accounts == {"@rcpt1": "a2"}
    with pytest.raises(ValidationError, match="recipient_accounts"):
        _request(recipient_accounts={"@stranger": "a1"})
    with pytest.raises(ValidationError, match="recipient_accounts"):
        _request(recipient_accounts={"@rcpt1": "a9"})


@pytest.mark.asyncio
async def test_each_recipient_gets_one_message_round_robin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job, calls = await _run(monkeypatch, _request())
    assert job.total == 5
    assert job.completed == 5
    assert job.status == "completed"
    assert calls == [
        ("a1", "rcpt1"),
        ("a2", "rcpt2"),
        ("a3", "rcpt3"),
        ("a4", "rcpt4"),
        ("a1", "rcpt5"),
    ]
    assert all(result.status == "ok" for result in job.results)


@pytest.mark.asyncio
async def test_one_recipient_is_written_by_the_first_account_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job, calls = await _run(monkeypatch, _request(recipients=["@rcpt1"]))
    assert job.total == 1
    assert calls == [("a1", "rcpt1")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "refusal",
    [
        {"status": "flood_wait", "flood_wait_seconds": 30},
        {"status": "peer_flood"},
        {"status": "failed", "error_type": "AccountNotFound"},
        {"status": "unavailable", "error_type": "ConnectionError"},
        {"status": "failed", "error_type": "ProfileGatewayError", "error_message": "session_dead"},
    ],
)
async def test_a_stopped_account_hands_its_recipient_on_and_leaves_the_rotation(
    monkeypatch: pytest.MonkeyPatch, refusal: dict[str, object]
) -> None:
    job, calls = await _run(monkeypatch, _request(), {("a2", "rcpt2"): refusal})
    assert calls == [
        ("a1", "rcpt1"),
        ("a2", "rcpt2"),
        ("a3", "rcpt2"),
        ("a4", "rcpt3"),
        ("a1", "rcpt4"),
        ("a3", "rcpt5"),
    ]
    assert job.completed == job.total == 5
    handed = [result for result in job.results if result.handed_over]
    assert [(r.account_id, r.recipient, r.status) for r in handed] == [("a2", "@rcpt2", "failed")]
    assert sum(result.status == "ok" for result in job.results) == 5


@pytest.mark.asyncio
async def test_unconfirmed_and_recipient_failures_are_never_resent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job, calls = await _run(
        monkeypatch,
        _request(recipients=["@rcpt1", "@rcpt2", "@rcpt3"]),
        {
            ("a1", "rcpt1"): {"status": "unavailable", "error_type": "UnconfirmedRequest"},
            ("a2", "rcpt2"): {"status": "failed", "error_type": "UserPrivacyRestrictedError"},
        },
    )
    assert calls == [("a1", "rcpt1"), ("a2", "rcpt2"), ("a3", "rcpt3")]
    assert _pairs(job) == [
        ("a1", "@rcpt1", "unconfirmed", False),
        ("a2", "@rcpt2", "failed", False),
        ("a3", "@rcpt3", "ok", False),
    ]


@pytest.mark.asyncio
async def test_when_every_account_stops_the_rest_are_skipped_without_duplicate_pairs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flood: dict[str, object] = {"status": "flood_wait", "flood_wait_seconds": 60}
    job, calls = await _run(
        monkeypatch,
        _request(account_ids=["a1", "a2"], recipients=["@rcpt1", "@rcpt2", "@rcpt3"]),
        {("a1", "rcpt1"): flood, ("a2", "rcpt1"): flood},
    )
    assert calls == [("a1", "rcpt1"), ("a2", "rcpt1")]
    assert _pairs(job) == [
        ("a1", "@rcpt1", "failed", True),
        ("a2", "@rcpt1", "failed", False),
        ("a2", "@rcpt2", "skipped", False),
        ("a1", "@rcpt3", "skipped", False),
    ]
    assert job.results[-1].retry_after_seconds == 60
    assert job.completed == job.total == 3
    assert len({(r.account_id, r.recipient) for r in job.results}) == len(job.results)


@pytest.mark.asyncio
async def test_a_pinned_recipient_goes_to_its_account_and_is_never_handed_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job, calls = await _run(
        monkeypatch,
        _request(
            account_ids=["a1", "a2"],
            recipients=["@rcpt1", "123", "@rcpt3", "456"],
            recipient_accounts={"123": "a1", "456": "a2"},
        ),
        {("a2", "rcpt3"): {"status": "peer_flood"}},
    )
    assert calls == [("a1", "rcpt1"), ("a1", "123"), ("a2", "rcpt3"), ("a1", "rcpt3")]
    assert _pairs(job) == [
        ("a1", "@rcpt1", "ok", False),
        ("a1", "123", "ok", False),
        ("a2", "@rcpt3", "failed", True),
        ("a1", "@rcpt3", "ok", False),
        ("a2", "456", "skipped", False),
    ]
    assert job.results[-1].error_code == "peer_flood"


@pytest.mark.asyncio
async def test_cancel_stops_split_before_the_next_recipient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    job = bulk_messages.start_bulk_message_job(_request(), "owner-1")

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
    assert calls == ["rcpt1"]


@pytest.mark.asyncio
async def test_cancel_after_a_refusal_does_not_report_a_hand_over_that_never_happened(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    job = bulk_messages.start_bulk_message_job(_request(), "owner-1")

    async def fake_execute(
        account_id: str,
        action: SendChatMessage,
        *,
        domain: str,  # noqa: ARG001
    ) -> ActionResult:
        calls.append((account_id, action.recipient))
        if account_id == "a2":
            bulk_messages.cancel_bulk_message_job(job.job_id, "owner-1")
            return ActionResult(
                status="flood_wait",
                action_type=action.action_type,
                account_id=account_id,
                flood_wait_seconds=30,
            )
        return ActionResult(status="ok", action_type=action.action_type, account_id=account_id)

    monkeypatch.setattr(bulk_messages, "execute", fake_execute)
    await bulk_messages.run_bulk_message_job(job.job_id)
    finished = bulk_messages.get_bulk_message_job(job.job_id, "owner-1")
    assert finished is not None
    assert calls == [("a1", "rcpt1"), ("a2", "rcpt2")]
    assert finished.status == "cancelled"
    assert _pairs(finished) == [
        ("a1", "@rcpt1", "ok", False),
        ("a2", "@rcpt2", "failed", False),
    ]
    assert finished.completed == 2


def test_a_user_id_with_leading_zeros_is_the_same_recipient() -> None:
    with pytest.raises(ValueError, match="duplicate recipients"):
        bulk_messages.start_bulk_message_job(_request(recipients=["123", "0123"]), "owner-1")
