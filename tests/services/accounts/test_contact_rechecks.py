"""Next-day re-checks of "not found" numbers by another account."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.repositories.outreach import save_outreach_limits
from core.repositories.recipient_history import list_contact_rechecks, queue_contact_recheck
from schemas.contact_lookup import ContactLookupJob, ContactLookupRequest
from schemas.outreach_limits import OutreachLimitsUpdate
from schemas.recipient_history import ContactRecheck
from schemas.recipient_rules import RecipientRules
from schemas.telegram_actions_contacts import ContactLookupBatchResult, ContactLookupMatch
from services.accounts import contact_lookup

if TYPE_CHECKING:
    from collections.abc import Iterator

    from schemas.telegram_actions import LookupContactsByPhone

_HIT = "+15550000100"
_MISS = "+15550000200"


@pytest.fixture(autouse=True)
def _empty_jobs() -> Iterator[None]:
    stores = (
        contact_lookup._jobs,
        contact_lookup._job_owners,
        contact_lookup._pending,
        contact_lookup._cancel_events,
    )
    for store in stores:
        store.clear()
    yield
    for store in stores:
        store.clear()


class _Telegram:
    """Records each ImportContacts batch; only ``_HIT`` is on Telegram, unless ``silent``."""

    def __init__(self, *, silent: frozenset[str] = frozenset()) -> None:
        self.silent = silent
        self.batches: list[tuple[str, list[str]]] = []

    async def __call__(
        self, account_id: str, action: LookupContactsByPhone
    ) -> ContactLookupBatchResult:
        self.batches.append((account_id, list(action.phones)))
        found = [] if account_id in self.silent else [p for p in action.phones if p == _HIT]
        return ContactLookupBatchResult(
            matches=[ContactLookupMatch(phone=p, user_id=100) for p in found],
            unresolved=[p for p in action.phones if p not in found],
        )


async def _run(
    monkeypatch: pytest.MonkeyPatch, telegram: _Telegram, **request: object
) -> ContactLookupJob:
    monkeypatch.setattr(contact_lookup, "execute_read", telegram)
    values: dict[str, object] = {"min_delay_seconds": 0, "max_delay_seconds": 0, **request}
    job = contact_lookup.start_contact_lookup_job(
        ContactLookupRequest.model_validate(values), "owner-1"
    )
    await contact_lookup.run_contact_lookup_job(job.job_id)
    finished = contact_lookup.get_contact_lookup_job(job.job_id, "owner-1")
    assert finished is not None
    return finished


async def _queued(phone: str, checked_by: str, *, due_in_hours: float) -> None:
    now = datetime.now(UTC)
    await queue_contact_recheck(
        ContactRecheck(
            phone_key=phone.removeprefix("+"),
            phone=phone,
            checked_by=checked_by,
            due_at=(now + timedelta(hours=due_in_hours)).isoformat(),
            created_at=now.isoformat(),
        )
    )


async def _queue() -> dict[str, str]:
    return {r.phone: r.checked_by for r in await list_contact_rechecks()}


@pytest.mark.asyncio
async def test_a_miss_is_queued_for_the_next_utc_day(monkeypatch: pytest.MonkeyPatch) -> None:
    await _run(monkeypatch, _Telegram(), account_ids=["a1"], phones=[_HIT, _MISS])

    [recheck] = await list_contact_rechecks()
    assert (recheck.phone, recheck.checked_by) == (_MISS, "a1")
    tomorrow = datetime.now(UTC).date() + timedelta(days=1)
    assert datetime.fromisoformat(recheck.due_at) == datetime.combine(
        tomorrow, datetime.min.time(), UTC
    )


@pytest.mark.asyncio
async def test_a_silently_limited_answer_is_queued_too(monkeypatch: pytest.MonkeyPatch) -> None:
    await save_outreach_limits(OutreachLimitsUpdate(silent_limit_streak=2))
    phones = [_HIT, "+15550000301", "+15550000302"]

    # a1 found someone before, then misses twice: its misses become "recheck" with no
    # account left to hand them to.
    await _run(monkeypatch, _Telegram(), account_ids=["a1"], phones=phones, batch_size=3)

    assert await _queue() == {"+15550000301": "a1", "+15550000302": "a1"}


@pytest.mark.asyncio
async def test_a_due_recheck_goes_to_another_account_and_ends_when_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _queued(_HIT, "a1", due_in_hours=-1)
    telegram = _Telegram()

    job = await _run(monkeypatch, telegram, account_ids=["a1", "a2"], phones=["+15550000001"])

    assert ("a2", [_HIT]) in telegram.batches
    assert all(_HIT not in batch for account, batch in telegram.batches if account == "a1")
    rechecked = [row for row in job.results if row.rechecked]
    assert [(r.phone, r.account_id, r.status) for r in rechecked] == [(_HIT, "a2", "found")]
    assert (job.completed, job.total) == (2, 2)
    # The typed miss is queued; the re-checked number is settled.
    assert await _queue() == {"+15550000001": "a1"}


@pytest.mark.asyncio
async def test_a_second_accounts_miss_ends_the_recheck(monkeypatch: pytest.MonkeyPatch) -> None:
    await _queued(_MISS, "a1", due_in_hours=-1)

    job = await _run(monkeypatch, _Telegram(), account_ids=["a1", "a2"], phones=[])

    assert [(r.account_id, r.status, r.rechecked) for r in job.results] == [
        ("a2", "not_found", True)
    ]
    assert await _queue() == {}


@pytest.mark.asyncio
async def test_a_recheck_waits_for_its_day_and_for_another_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _queued(_HIT, "a2", due_in_hours=2)
    await _queued(_MISS, "a1", due_in_hours=-1)
    telegram = _Telegram()

    job = await _run(monkeypatch, telegram, account_ids=["a1"], phones=["+15550000001"])

    assert telegram.batches == [("a1", ["+15550000001"])]
    assert job.total == 1
    assert await _queue() == {_HIT: "a2", _MISS: "a1", "+15550000001": "a1"}


@pytest.mark.asyncio
async def test_the_operators_numbers_come_before_rechecks(monkeypatch: pytest.MonkeyPatch) -> None:
    await _queued(_HIT, "a1", due_in_hours=-1)
    telegram = _Telegram()

    await _run(monkeypatch, telegram, account_ids=["a2"], phones=["+15550000001"], daily_cap=1)

    assert telegram.batches == [("a2", ["+15550000001"])]
    assert _HIT in await _queue()


@pytest.mark.asyncio
async def test_switched_off_rechecks_neither_queue_nor_run(monkeypatch: pytest.MonkeyPatch) -> None:
    await _queued(_HIT, "a1", due_in_hours=-1)
    await save_outreach_limits(
        OutreachLimitsUpdate(recipients=RecipientRules(recheck_not_found=False)),
    )
    telegram = _Telegram()

    await _run(monkeypatch, telegram, account_ids=["a2"], phones=[_MISS])

    assert telegram.batches == [("a2", [_MISS])]
    assert await _queue() == {_HIT: "a1"}
    # The run's own switch outranks the saved one.
    await _run(monkeypatch, telegram, account_ids=["a2"], phones=[], recheck_not_found=True)
    assert telegram.batches[-1] == ("a2", [_HIT])


def test_an_empty_list_is_refused_when_rechecks_are_off() -> None:
    with pytest.raises(ValueError, match="no valid phones"):
        contact_lookup.start_contact_lookup_job(
            ContactLookupRequest(account_ids=["a1"], phones=[], recheck_not_found=False),
            "owner-1",
        )
    with pytest.raises(ValueError, match="no valid phones"):
        contact_lookup.start_contact_lookup_job(
            ContactLookupRequest(account_ids=["a1"], phones=["junk"]), "owner-1"
        )


@pytest.mark.asyncio
async def test_the_summary_counts_waiting_and_due_numbers() -> None:
    await _queued(_HIT, "a1", due_in_hours=-1)
    await _queued(_MISS, "a1", due_in_hours=5)

    summary = await contact_lookup.get_contact_rechecks()

    assert (summary.waiting, summary.due) == (2, 1)
