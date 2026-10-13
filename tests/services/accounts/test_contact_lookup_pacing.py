"""Contact-lookup pacing: daily import allowance, batch size, saved pauses, silent limit."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from core.repositories.outreach import add_outreach_usage, list_outreach_usage, save_outreach_limits
from schemas.contact_lookup import ContactLookupJob, ContactLookupRequest
from schemas.outreach_limits import OutreachDailyUsage, OutreachLimitsUpdate
from schemas.telegram_actions_contacts import ContactLookupBatchResult, ContactLookupMatch
from services.accounts import contact_lookup

if TYPE_CHECKING:
    from collections.abc import Iterator

    from schemas.telegram_actions import LookupContactsByPhone

_HIT = "+15550000100"


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


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def _phones(count: int, start: int = 1) -> list[str]:
    return [f"+1555000{index:04d}" for index in range(start, start + count)]


class _Telegram:
    """Records each ImportContacts batch; ``silent`` accounts never find anyone."""

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
    monkeypatch: pytest.MonkeyPatch,
    telegram: _Telegram,
    **request: object,
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


def _final(job: ContactLookupJob) -> dict[str, tuple[str, str, str | None]]:
    return {
        row.phone: (row.account_id, row.status, row.error_code)
        for row in job.results
        if not row.handed_over
    }


@pytest.mark.asyncio
async def test_a_run_never_exceeds_the_accounts_daily_allowance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await add_outreach_usage(OutreachDailyUsage(account_id="a1", day=_today(), contact_imports=13))
    telegram = _Telegram()

    job = await _run(monkeypatch, telegram, account_ids=["a1"], phones=_phones(5))

    assert [phones for _account, phones in telegram.batches] == [_phones(2)]
    final = _final(job)
    assert [final[p][1] for p in _phones(2)] == ["not_found", "not_found"]
    # Never checked: not "not on Telegram", so the operator re-runs them tomorrow.
    assert [final[p][1:] for p in _phones(3, start=3)] == [("skipped", "daily_limit")] * 3
    assert job.completed == job.total == 5
    usage = await list_outreach_usage(["a1"], _today())
    assert usage["a1"].contact_imports == 15


@pytest.mark.asyncio
async def test_the_allowance_is_shared_by_runs_of_the_same_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await save_outreach_limits(OutreachLimitsUpdate(contact_imports_per_day=2))
    await _run(monkeypatch, _Telegram(), account_ids=["a1"], phones=_phones(2))
    telegram = _Telegram()

    job = await _run(monkeypatch, telegram, account_ids=["a1"], phones=_phones(2, start=10))

    assert telegram.batches == []
    assert {status for _a, status, _c in _final(job).values()} == {"skipped"}


@pytest.mark.asyncio
async def test_a_full_account_passes_its_numbers_to_one_with_room(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await add_outreach_usage(OutreachDailyUsage(account_id="a1", day=_today(), contact_imports=14))
    telegram = _Telegram()

    job = await _run(monkeypatch, telegram, account_ids=["a1", "a2"], phones=_phones(4))

    assert {p: a for p, (a, _s, _c) in _final(job).items()} == {
        _phones(4)[0]: "a1",
        _phones(4)[1]: "a2",
        _phones(4)[2]: "a2",
        _phones(4)[3]: "a2",
    }


@pytest.mark.asyncio
async def test_batches_default_to_the_saved_size_and_a_run_may_override_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telegram = _Telegram()
    await _run(monkeypatch, telegram, account_ids=["a1"], phones=_phones(7))
    assert [len(phones) for _a, phones in telegram.batches] == [3, 3, 1]

    contact_lookup._jobs.clear()
    telegram = _Telegram()
    await _run(monkeypatch, telegram, account_ids=["a2"], phones=_phones(7), batch_size=5)
    assert [len(phones) for _a, phones in telegram.batches] == [5, 2]


@pytest.mark.asyncio
async def test_pauses_left_out_of_the_form_come_from_the_saved_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await save_outreach_limits(
        OutreachLimitsUpdate(lookup_min_delay_seconds=240, lookup_max_delay_seconds=900),
    )
    jitter: list[tuple[float, float]] = []

    def fake_uniform(low: float, high: float) -> float:
        jitter.append((low, high))
        return 0

    monkeypatch.setattr(contact_lookup.random, "uniform", fake_uniform)
    monkeypatch.setattr(contact_lookup, "execute_read", _Telegram())
    job = contact_lookup.start_contact_lookup_job(
        ContactLookupRequest(account_ids=["a1"], phones=_phones(1)), "owner-1"
    )
    await contact_lookup.run_contact_lookup_job(job.job_id)

    assert jitter == [(240, 900)]


def test_a_request_sends_both_pauses_or_neither() -> None:
    with pytest.raises(ValueError, match="both delays"):
        ContactLookupRequest(account_ids=["a1"], phones=_phones(1), min_delay_seconds=5)
    with pytest.raises(ValueError, match="less than or equal to 20"):
        ContactLookupRequest(account_ids=["a1"], phones=_phones(1), batch_size=21)
    request = ContactLookupRequest(
        account_ids=["a1"], phones=_phones(1), min_delay_seconds=3600, max_delay_seconds=3600
    )
    assert request.max_delay_seconds == 3600


@pytest.mark.asyncio
async def test_a_silently_limited_account_hands_its_misses_to_one_that_finds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await save_outreach_limits(OutreachLimitsUpdate(silent_limit_streak=3))
    # Round-robin: a1 gets p0, p2, p4; a2 gets the hit, p3, p5.
    phones = [*_phones(1), _HIT, *_phones(4, start=2)]
    telegram = _Telegram(silent=frozenset({"a1"}))

    job = await _run(monkeypatch, telegram, account_ids=["a1", "a2"], phones=phones, batch_size=2)

    a1_phones = phones[0::2]
    # a1: [p0, p2] empty, then a2 finds the hit, then a1's [p4] makes three misses.
    assert [account for account, _phones in telegram.batches[:3]] == ["a1", "a2", "a1"]
    assert all(account == "a2" for account, _phones in telegram.batches[3:])
    history = [row for row in job.results if row.handed_over]
    assert {row.phone for row in history} == set(a1_phones)
    assert {(row.account_id, row.status, row.error_code) for row in history} == {
        ("a1", "recheck", "silent_limit")
    }
    final = _final(job)
    assert {final[p] for p in a1_phones} == {("a2", "not_found", None)}
    assert final[_HIT] == ("a2", "found", None)
    # a2 misses p3, p5 and the three re-checks, yet only its own two misses count.
    assert final[phones[5]] == ("a2", "not_found", None)
    assert job.completed == job.total == len(phones)


@pytest.mark.asyncio
async def test_misses_stay_marked_for_recheck_when_no_account_has_room(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await save_outreach_limits(
        OutreachLimitsUpdate(silent_limit_streak=2, contact_imports_per_day=3),
    )
    phones = [*_phones(1), _HIT, *_phones(4, start=2)]
    telegram = _Telegram(silent=frozenset({"a1"}))

    job = await _run(monkeypatch, telegram, account_ids=["a1", "a2"], phones=phones, batch_size=1)

    final = _final(job)
    # a1 misses p0, a2 finds the hit, a1 misses p2: the streak trips, a2 is full.
    assert final[phones[0]] == ("a1", "recheck", "silent_limit")
    assert final[phones[2]] == ("a1", "recheck", "silent_limit")
    assert final[phones[4]] == ("a1", "skipped", "silent_limit")
    assert not any(row.handed_over for row in job.results)
    assert job.completed == job.total


@pytest.mark.asyncio
async def test_a_list_where_nobody_is_on_telegram_trips_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await save_outreach_limits(OutreachLimitsUpdate(silent_limit_streak=2))
    telegram = _Telegram(silent=frozenset({"a1", "a2"}))

    job = await _run(
        monkeypatch, telegram, account_ids=["a1", "a2"], phones=_phones(8), batch_size=1
    )

    assert len(telegram.batches) == 8
    assert {status for _a, status, _c in _final(job).values()} == {"not_found"}


@pytest.mark.asyncio
async def test_an_account_that_found_people_before_trips_on_its_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await save_outreach_limits(OutreachLimitsUpdate(silent_limit_streak=2))
    await add_outreach_usage(
        OutreachDailyUsage(account_id="a1", day="2026-01-01", contact_matches=3)
    )
    telegram = _Telegram(silent=frozenset({"a1"}))

    job = await _run(monkeypatch, telegram, account_ids=["a1"], phones=_phones(4), batch_size=1)

    assert len(telegram.batches) == 2
    final = _final(job)
    assert [final[p][1:] for p in _phones(4)] == [
        ("recheck", "silent_limit"),
        ("recheck", "silent_limit"),
        ("skipped", "silent_limit"),
        ("skipped", "silent_limit"),
    ]


@pytest.mark.asyncio
async def test_matches_are_counted_for_the_history(monkeypatch: pytest.MonkeyPatch) -> None:
    await _run(monkeypatch, _Telegram(), account_ids=["a1"], phones=[_HIT, *_phones(1)])

    usage = await list_outreach_usage(["a1"], _today())
    assert (usage["a1"].contact_imports, usage["a1"].contact_matches) == (2, 1)


@pytest.mark.asyncio
async def test_a_run_may_set_its_own_daily_allowance(monkeypatch: pytest.MonkeyPatch) -> None:
    await add_outreach_usage(OutreachDailyUsage(account_id="a1", day=_today(), contact_imports=10))
    telegram = _Telegram()

    # The saved limit is 15, so 5 would fit; the run's own 12 leaves room for 2.
    job = await _run(monkeypatch, telegram, account_ids=["a1"], phones=_phones(5), daily_cap=12)

    assert sum(len(phones) for _a, phones in telegram.batches) == 2
    assert sum(status == "skipped" for _a, status, _c in _final(job).values()) == 3
