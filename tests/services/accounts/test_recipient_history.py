"""Recipient history: no repeat within N days, one client — one account."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.repositories.outreach import save_outreach_limits
from core.repositories.recipient_history import add_recipient_contact, find_recipient_contacts
from schemas.bulk_messages import BulkMessageJob, BulkMessageRequest
from schemas.outreach_limits import OutreachLimitsUpdate
from schemas.recipient_history import RecipientContact
from schemas.recipient_rules import RecipientRules
from schemas.telegram_actions import ActionResult, SendChatMessage
from services.accounts import bulk_messages
from tests.services.accounts.conftest import ALWAYS_OPEN

pytestmark = pytest.mark.usefixtures("empty_bulk_jobs")

_ONE_ACCOUNT_ONLY = {"no_repeat_days": None, "one_account_per_client": True}


async def _run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    errors: dict[tuple[str, str], str] | None = None,
    **request: object,
) -> tuple[BulkMessageJob, list[tuple[str, str]]]:
    """One stored run to its end; ``errors`` makes an (account, peer) send fail."""
    calls: list[tuple[str, str]] = []

    async def fake_execute(
        account_id: str,
        action: SendChatMessage,
        *,
        domain: str,  # noqa: ARG001
    ) -> ActionResult:
        calls.append((account_id, action.recipient))
        error = (errors or {}).get((account_id, action.recipient))
        if error is None:
            return ActionResult(status="ok", action_type=action.action_type, account_id=account_id)
        return ActionResult(
            status="failed",
            action_type=action.action_type,
            account_id=account_id,
            error_type=error,
        )

    monkeypatch.setattr(bulk_messages, "execute", fake_execute)
    values: dict[str, object] = {
        "account_ids": ["a1"],
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


def _outcomes(job: BulkMessageJob) -> dict[tuple[str, str], tuple[str, str | None]]:
    return {(r.account_id, r.recipient): (r.status, r.error_code) for r in job.results}


async def _wrote(account_id: str, peer: str, *, days_ago: float, phone: str | None = None) -> None:
    sent_at = datetime.now(UTC) - timedelta(days=days_ago)
    await add_recipient_contact(
        RecipientContact(peer=peer, phone=phone, account_id=account_id, sent_at=sent_at.isoformat())
    )


@pytest.mark.asyncio
async def test_every_send_that_may_have_left_is_remembered_without_its_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _run(
        monkeypatch,
        account_ids=["a1"],
        recipients=["@Client", "555", "@refused"],
        recipient_phones={"@Client": "+7 (916) 123-45-67"},
        errors={("a1", "refused"): "BadRequest"},
    )

    contacts = await find_recipient_contacts(["client", "555", "refused"], [])
    # Keyed by the casefolded username or user id plus the phone's digits; a send
    # Telegram refused never reached anyone and is not history.
    assert [(c.peer, c.phone, c.account_id) for c in contacts] == [
        ("client", "79161234567", "a1"),
        ("555", None, "a1"),
    ]


@pytest.mark.asyncio
async def test_a_recipient_written_to_within_the_window_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _wrote("a2", "recent", days_ago=29)
    await _wrote("a2", "old", days_ago=31)

    job, calls = await _run(
        monkeypatch,
        recipients=["@recent", "@old"],
        recipient_rules={"no_repeat_days": 30, "one_account_per_client": False},
    )

    assert calls == [("a1", "old")]
    assert _outcomes(job) == {
        ("a1", "@recent"): ("skipped", "recently_messaged"),
        ("a1", "@old"): ("ok", None),
    }
    assert (job.completed, job.total) == (2, 2)


@pytest.mark.asyncio
async def test_a_client_found_again_by_phone_is_known_under_a_new_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _wrote("a1", "4242", days_ago=1, phone="79161234567")

    job, calls = await _run(
        monkeypatch,
        recipients=["@renamed"],
        recipient_phones={"@renamed": "+79161234567"},
    )

    assert calls == []
    assert _outcomes(job) == {("a1", "@renamed"): ("skipped", "recently_messaged")}


@pytest.mark.asyncio
async def test_a_client_goes_back_to_the_account_that_wrote_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _wrote("a2", "client", days_ago=60)
    await _wrote("a1", "client", days_ago=40)

    # Round-robin would give @client to a1 and a lookup pinned it to a1 too; a2 wrote
    # first, so a2 writes again.
    job, calls = await _run(
        monkeypatch,
        account_ids=["a1", "a2"],
        recipients=["@client", "@fresh"],
        recipient_accounts={"@client": "a1"},
        mode="split",
        recipient_rules=_ONE_ACCOUNT_ONLY,
    )

    assert sorted(calls) == [("a1", "fresh"), ("a2", "client")]
    assert job.results[0].status == "ok"


@pytest.mark.asyncio
async def test_a_client_of_an_account_left_out_of_the_run_is_reported_not_moved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _wrote("a9", "client", days_ago=60)

    job, calls = await _run(
        monkeypatch,
        account_ids=["a1", "a2"],
        recipients=["@client"],
        mode="split",
        recipient_rules=_ONE_ACCOUNT_ONLY,
    )

    assert calls == []
    assert _outcomes(job) == {("a1", "@client"): ("skipped", "owned_by_other_account")}


@pytest.mark.asyncio
async def test_each_mode_lets_only_the_owner_write_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _wrote("a2", "client", days_ago=60)

    job, calls = await _run(
        monkeypatch,
        account_ids=["a1", "a2"],
        recipients=["@client"],
        recipient_rules=_ONE_ACCOUNT_ONLY,
    )

    assert calls == [("a2", "client")]
    assert _outcomes(job) == {
        ("a1", "@client"): ("skipped", "owned_by_other_account"),
        ("a2", "@client"): ("ok", None),
    }


@pytest.mark.asyncio
async def test_a_stopped_owner_does_not_hand_its_client_to_another_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _wrote("a1", "client", days_ago=60)

    job, calls = await _run(
        monkeypatch,
        account_ids=["a1", "a2"],
        recipients=["@client"],
        mode="split",
        recipient_rules=_ONE_ACCOUNT_ONLY,
        errors={("a1", "client"): "PeerFlood"},
    )

    assert calls == [("a1", "client")]
    assert [(r.account_id, r.handed_over) for r in job.results] == [("a1", False)]


@pytest.mark.asyncio
async def test_rules_switched_off_for_one_run_write_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _wrote("a2", "client", days_ago=1)

    _, calls = await _run(
        monkeypatch,
        recipients=["@client"],
        recipient_rules={"no_repeat_days": None, "one_account_per_client": False},
    )

    assert calls == [("a1", "client")]


@pytest.mark.asyncio
async def test_a_run_without_rules_follows_the_saved_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _wrote("a1", "client", days_ago=10)
    await save_outreach_limits(
        OutreachLimitsUpdate(recipients=RecipientRules(no_repeat_days=7)),
    )

    _, calls = await _run(monkeypatch, recipients=["@client"])

    assert calls == [("a1", "client")]
