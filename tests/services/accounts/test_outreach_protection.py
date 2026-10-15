"""Account protection: young accounts, accounts left out, rests, and the contact lookup."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError
from sqlalchemy import update

from core.db import _accounts, _get_engine, create_account, upsert_spam_status
from core.repositories.outreach_rests import list_outreach_rests, record_outreach_rest
from core.repositories.warming_floods import record_warming_flood_event
from core.telegram_client import TelegramReadError
from schemas.accounts import AccountCreate
from schemas.contact_lookup import ContactLookupJob, ContactLookupRequest
from schemas.outreach_protection import OutreachProtection, OutreachRest, SkippedAccount
from schemas.spam_status import SpamStatusVerdict
from schemas.telegram_actions_contacts import ContactLookupBatchResult
from schemas.warming_flood import WarmingFloodEventWrite
from services.accounts import contact_lookup
from services.accounts._outreach_protection import (
    halve,
    record_limit,
    skipped_accounts,
    young_account_ids,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from schemas.telegram_actions import LookupContactsByPhone

_ON = OutreachProtection()


def _now() -> datetime:
    return datetime.now(UTC)


async def _account(
    account_id: str, *, days_in_fleet: int = 0, registered: date | None = None
) -> None:
    await create_account(AccountCreate(account_id=account_id, registered_at=registered))
    if days_in_fleet:
        joined = (_now() - timedelta(days=days_in_fleet)).isoformat()
        with _get_engine().begin() as connection:
            connection.execute(
                update(_accounts)
                .where(_accounts.c.account_id == account_id)
                .values(created_at=joined)
            )


async def _rest(account_id: str, *, limited_ago: timedelta, rest_left: timedelta) -> None:
    await record_outreach_rest(
        OutreachRest(
            account_id=account_id,
            reason="flood_wait",
            limited_at=(_now() - limited_ago).isoformat(),
            rest_until=(_now() + rest_left).isoformat(),
        )
    )


def test_a_young_cap_is_half_rounded_down_and_never_zero() -> None:
    assert [halve(cap) for cap in (15, 25, 2, 1)] == [7, 12, 1, 1]


def test_typing_range_must_not_be_upside_down() -> None:
    with pytest.raises(ValidationError, match="typing max"):
        OutreachProtection(typing_min_seconds=6, typing_max_seconds=2)


@pytest.mark.asyncio
async def test_age_is_the_registration_date_when_known_else_time_in_the_fleet() -> None:
    week_ago = (_now() - timedelta(days=7)).date()
    await _account("fresh")
    await _account("veteran", days_in_fleet=40)
    await _account("old_registration", registered=date(2020, 1, 1))
    await _account("new_registration", days_in_fleet=40, registered=week_ago)
    ids = ["fresh", "veteran", "old_registration", "new_registration", "not_in_fleet"]

    assert await young_account_ids(ids, _ON, _now()) == {"fresh", "new_registration"}
    assert await young_account_ids(
        ids, _ON.model_copy(update={"young_account_days": 5}), _now()
    ) == {"fresh"}
    off = _ON.model_copy(update={"young_half_caps": False})
    assert await young_account_ids(ids, off, _now()) == set()


@pytest.mark.asyncio
async def test_resting_spam_blocked_and_recently_flooded_accounts_are_left_out() -> None:
    for account_id in ("resting", "spam", "warm_flood", "old_flood", "clean", "rested"):
        await _account(account_id)
    await _rest("resting", limited_ago=timedelta(hours=30), rest_left=timedelta(hours=3))
    await _rest("rested", limited_ago=timedelta(hours=30), rest_left=-timedelta(hours=1))
    await upsert_spam_status(
        SpamStatusVerdict(account_id="spam", status="limited", checked_at=_now().isoformat())
    )
    await upsert_spam_status(
        SpamStatusVerdict(account_id="clean", status="clean", checked_at=_now().isoformat())
    )
    await record_warming_flood_event(
        WarmingFloodEventWrite(
            account_id="warm_flood", created_at=(_now() - timedelta(hours=2)).isoformat()
        )
    )
    await record_warming_flood_event(
        WarmingFloodEventWrite(
            account_id="old_flood", created_at=(_now() - timedelta(hours=30)).isoformat()
        )
    )
    ids = ["clean", "resting", "spam", "warm_flood", "old_flood", "rested"]

    skipped = await skipped_accounts(ids, _ON, _now())

    assert [(s.account_id, s.reason) for s in skipped] == [
        ("resting", "account_resting"),
        ("spam", "account_spam_blocked"),
        ("warm_flood", "account_recent_flood"),
    ]
    assert 3 * 3600 - 60 < (skipped[0].retry_after_seconds or 0) <= 3 * 3600
    injured_off = _ON.model_copy(update={"skip_injured": False})
    assert [s.account_id for s in await skipped_accounts(ids, injured_off, _now())] == ["resting"]
    rest_off = injured_off.model_copy(update={"rest_after_limit": False})
    assert await skipped_accounts(ids, rest_off, _now()) == []


@pytest.mark.asyncio
async def test_an_outreach_limit_in_the_last_day_counts_as_a_flood_even_after_its_rest() -> None:
    await _rest("a1", limited_ago=timedelta(hours=5), rest_left=-timedelta(hours=1))

    assert await skipped_accounts(["a1"], _ON, _now()) == [
        SkippedAccount(account_id="a1", reason="account_recent_flood")
    ]


@pytest.mark.asyncio
async def test_a_limit_rests_the_longer_of_the_setting_and_the_wait_and_never_shortens() -> None:
    now = _now()
    await record_limit("a1", "flood_wait", None, _ON, now)
    first = (await list_outreach_rests(["a1"]))["a1"]
    assert datetime.fromisoformat(first.rest_until) == now + timedelta(hours=12)

    await record_limit("a1", "peer_flood", 20 * 3600, _ON, now)
    longer = (await list_outreach_rests(["a1"]))["a1"]
    assert (longer.reason, datetime.fromisoformat(longer.rest_until)) == (
        "peer_flood",
        now + timedelta(hours=20),
    )

    later = now + timedelta(hours=1)
    await record_limit("a1", "flood_wait", None, _ON.model_copy(update={"rest_hours": 1}), later)
    kept = (await list_outreach_rests(["a1"]))["a1"]
    assert kept.limited_at == later.isoformat()
    assert datetime.fromisoformat(kept.rest_until) == now + timedelta(hours=20)

    await record_limit(
        "a2", "flood_wait", 600, _ON.model_copy(update={"rest_after_limit": False}), now
    )
    assert (await list_outreach_rests(["a2"]))["a2"].rest_until == now.isoformat()


# --- the contact lookup -----------------------------------------------------------


@pytest.fixture
def _empty_lookups() -> Iterator[None]:
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


def _phones(count: int) -> list[str]:
    return [f"+1555000{index:04d}" for index in range(1, count + 1)]


class _Telegram:
    """Records each ImportContacts batch; ``limited`` accounts get their numbers deferred."""

    def __init__(self, limited: frozenset[str] = frozenset()) -> None:
        self.limited = limited
        self.batches: list[tuple[str, list[str]]] = []

    async def __call__(
        self, account_id: str, action: LookupContactsByPhone
    ) -> ContactLookupBatchResult:
        self.batches.append((account_id, list(action.phones)))
        if account_id in self.limited:
            return ContactLookupBatchResult(matches=[], unresolved=[], retry=list(action.phones))
        return ContactLookupBatchResult(matches=[], unresolved=list(action.phones))


async def _lookup(
    monkeypatch: pytest.MonkeyPatch, telegram: object, **request: object
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


@pytest.mark.asyncio
@pytest.mark.usefixtures("_empty_lookups")
async def test_a_young_account_imports_half_its_daily_allowance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _account("young")
    await _account("grown", days_in_fleet=40)
    telegram = _Telegram()

    job = await _lookup(
        monkeypatch,
        telegram,
        account_ids=["young", "grown"],
        phones=_phones(12),
        daily_cap=4,
        batch_size=1,
    )

    checked = [account_id for account_id, _ in telegram.batches]
    assert (checked.count("young"), checked.count("grown")) == (2, 4)
    assert sum(r.error_code == "daily_limit" for r in job.results) == 6


@pytest.mark.asyncio
@pytest.mark.usefixtures("_empty_lookups")
async def test_left_out_accounts_check_nothing_and_are_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _rest("resting", limited_ago=timedelta(hours=30), rest_left=timedelta(hours=2))
    telegram = _Telegram()

    job = await _lookup(monkeypatch, telegram, account_ids=["resting", "a2"], phones=_phones(4))

    assert {account_id for account_id, _ in telegram.batches} == {"a2"}
    assert [(s.account_id, s.reason) for s in job.skipped_accounts] == [
        ("resting", "account_resting")
    ]
    assert job.completed == job.total == 4

    only_resting = await _lookup(monkeypatch, telegram, account_ids=["resting"], phones=_phones(2))
    assert [(r.status, r.error_code) for r in only_resting.results] == [
        ("skipped", "account_resting"),
        ("skipped", "account_resting"),
    ]
    assert only_resting.status == "completed"


@pytest.mark.asyncio
@pytest.mark.usefixtures("_empty_lookups")
async def test_a_rate_limited_lookup_account_rests_and_two_of_them_stop_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def flooded(account_id: str, action: LookupContactsByPhone) -> ContactLookupBatchResult:
        if account_id == "a2":
            reason = "FloodWait(30s)"
            raise TelegramReadError(reason, kind="flood_wait", seconds=30)
        return await telegram(account_id, action)

    telegram = _Telegram(limited=frozenset({"a1"}))

    job = await _lookup(
        monkeypatch, flooded, account_ids=["a1", "a2", "a3"], phones=_phones(9), batch_size=1
    )

    rests = await list_outreach_rests(["a1", "a2", "a3"])
    assert {account_id: rest.reason for account_id, rest in rests.items()} == {
        "a1": "flood_wait",
        "a2": "flood_wait",
    }
    # a1 and a2 are served first and both hit a limit: a3's whole queue is skipped
    # with the run before it checks anything.
    assert [account_id for account_id, _ in telegram.batches] == ["a1"]
    assert sum(r.error_code == "run_stopped_on_limits" for r in job.results) == 3
    assert job.completed == job.total == 9

    contact_lookup._jobs.clear()
    kept_going = await _lookup(
        monkeypatch,
        flooded,
        account_ids=["b1", "a2", "b3"],
        phones=_phones(9),
        batch_size=1,
        protection={"stop_run_on_limits": False, "skip_injured": False, "rest_after_limit": False},
    )
    assert not any(r.error_code == "run_stopped_on_limits" for r in kept_going.results)
