"""Contact-lookup run: normalisation, distribution, batching, flood-stop, cancel."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import pytest
from pydantic import ValidationError

from core.telegram_client import TelegramReadError
from schemas.contact_lookup import ContactLookupRequest
from schemas.telegram_actions_contacts import ContactLookupBatchResult, ContactLookupMatch
from services.accounts import contact_lookup

if TYPE_CHECKING:
    from collections.abc import Iterator

    from schemas.telegram_actions import LookupContactsByPhone

# Numbers whose normalised form maps to a Telegram user, keyed by canonical phone.
_FOUND: dict[str, tuple[int, str | None]] = {
    "+15551110000": (111, "alice"),
    "+15552220000": (222, None),
}


@pytest.fixture(autouse=True)
def _empty_jobs() -> Iterator[None]:
    for store in (
        contact_lookup._jobs,
        contact_lookup._job_owners,
        contact_lookup._pending,
        contact_lookup._cancel_events,
    ):
        store.clear()
    yield
    for store in (
        contact_lookup._jobs,
        contact_lookup._job_owners,
        contact_lookup._pending,
        contact_lookup._cancel_events,
    ):
        store.clear()


def _request(**overrides: object) -> ContactLookupRequest:
    values: dict[str, object] = {
        "account_ids": ["a1", "a2"],
        "phones": ["+15551110000", "+15552220000"],
        "min_delay_seconds": 0,
        "max_delay_seconds": 0,
    }
    values.update(overrides)
    return ContactLookupRequest.model_validate(values)


async def _fake_lookup(_account_id: str, action: LookupContactsByPhone) -> ContactLookupBatchResult:
    matches: list[ContactLookupMatch] = []
    unresolved: list[str] = []
    for phone in action.phones:
        found = _FOUND.get(phone)
        if found is None:
            unresolved.append(phone)
            continue
        user_id, username = found
        matches.append(
            ContactLookupMatch(phone=phone, user_id=user_id, username=username, first_name="Name")
        )
    return ContactLookupBatchResult(matches=matches, unresolved=unresolved)


def test_invalid_requests_are_rejected() -> None:
    with pytest.raises(ValidationError):
        _request(max_delay_seconds=0, min_delay_seconds=1)
    with pytest.raises(ValidationError):
        _request(account_ids=["a1", "a1"])
    with pytest.raises(ValidationError):
        _request(phones=[" "])
    with pytest.raises(ValidationError):
        _request(phones=["9" * 41])


def test_normalisation_dedupes_across_formats() -> None:
    job = contact_lookup.start_contact_lookup_job(
        _request(
            account_ids=["a1"],
            phones=["+1 (555) 111-0000", "+15551110000", "garbage", "12"],
        ),
        "owner-1",
    )
    # The two spellings collapse to one lookup; junk and too-short entries drop out.
    assert job.total == 1
    _data, distribution = contact_lookup._pending[job.job_id]
    assert distribution == {"a1": ["+15551110000"]}


def test_plus_and_bare_spellings_of_one_number_collapse() -> None:
    # Telethon strips the "+" before the RPC, so both would be the same lookup twice
    # (and, round-robin, on two accounts): the first spelling seen is kept.
    job = contact_lookup.start_contact_lookup_job(
        _request(account_ids=["a1", "a2"], phones=["+15551110000", "15551110000"]),
        "owner-1",
    )
    assert job.total == 1
    _data, distribution = contact_lookup._pending[job.job_id]
    assert distribution == {"a1": ["+15551110000"], "a2": []}


def test_copied_numbers_keep_working_despite_invisible_marks() -> None:
    # Contacts apps wrap a copied number in direction marks and may use U+2011 dashes.
    job = contact_lookup.start_contact_lookup_job(
        _request(
            account_ids=["a1"],
            phones=["\u202a+1 (555) 111\u20110000\u202c", "\u200e+1 555 222\u22120000"],
        ),
        "owner-1",
    )
    _data, distribution = contact_lookup._pending[job.job_id]
    assert distribution == {"a1": ["+15551110000", "+15552220000"]}


def test_only_separators_are_stripped_never_letters_or_list_delimiters() -> None:
    job = contact_lookup.start_contact_lookup_job(
        _request(
            account_ids=["a1"],
            phones=["+1.555.111.0000", "call 15552220000", "12345, 67890", "+7 999+1234567"],
        ),
        "owner-1",
    )
    # Two short numbers on one line must not fuse into a third, fabricated one.
    _data, distribution = contact_lookup._pending[job.job_id]
    assert distribution == {"a1": ["+15551110000"]}


def test_phones_are_spread_round_robin_across_accounts() -> None:
    job = contact_lookup.start_contact_lookup_job(
        _request(phones=["+15550000001", "+15550000002", "+15550000003", "+15550000004"]),
        "owner-1",
    )
    _data, distribution = contact_lookup._pending[job.job_id]
    assert distribution == {
        "a1": ["+15550000001", "+15550000003"],
        "a2": ["+15550000002", "+15550000004"],
    }


def test_second_run_is_refused_while_one_is_active() -> None:
    contact_lookup.start_contact_lookup_job(_request(), "owner-1")
    with pytest.raises(ValueError, match="contact_lookup_run_active"):
        contact_lookup.start_contact_lookup_job(_request(), "owner-1")


def test_owner_scoping_on_lookup_and_cancel() -> None:
    job = contact_lookup.start_contact_lookup_job(_request(), "owner-1")
    assert contact_lookup.get_active_contact_lookup_job("owner-1") == job
    assert contact_lookup.get_active_contact_lookup_job("owner-2") is None
    assert contact_lookup.get_contact_lookup_job(job.job_id, "owner-2") is None
    assert contact_lookup.cancel_contact_lookup_job(job.job_id, "owner-2") is None
    assert not contact_lookup._cancel_events[job.job_id].is_set()


@pytest.mark.asyncio
async def test_run_maps_found_and_not_found_per_phone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(contact_lookup, "execute_read", _fake_lookup)
    job = contact_lookup.start_contact_lookup_job(
        _request(account_ids=["a1"], phones=["+15551110000", "+15552220000", "+15559990000"]),
        "owner-1",
    )
    await contact_lookup.run_contact_lookup_job(job.job_id)

    finished = contact_lookup.get_contact_lookup_job(job.job_id, "owner-1")
    assert finished is not None
    assert finished.status == "completed"
    assert finished.completed == 3
    by_phone = {row.phone: row for row in finished.results}
    assert by_phone["+15551110000"].status == "found"
    assert by_phone["+15551110000"].username == "alice"
    assert by_phone["+15551110000"].display_name == "Name"
    # A found user with no username still carries the id for the send step.
    assert by_phone["+15552220000"].status == "found"
    assert by_phone["+15552220000"].user_id == 222
    assert by_phone["+15552220000"].username is None
    assert by_phone["+15559990000"].status == "not_found"


@pytest.mark.asyncio
async def test_flood_stops_the_account_and_skips_its_remaining_numbers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(contact_lookup, "_BATCH_SIZE", 1)

    async def flooded(account_id: str, action: LookupContactsByPhone) -> ContactLookupBatchResult:
        if account_id == "a1":
            reason = "FloodWait(30s)"
            raise TelegramReadError(reason, kind="flood_wait", seconds=30)
        return await _fake_lookup(account_id, action)

    monkeypatch.setattr(contact_lookup, "execute_read", flooded)
    job = contact_lookup.start_contact_lookup_job(
        _request(
            account_ids=["a1", "a2"],
            phones=["+15551110000", "+15550000009", "+15552220000", "+15550000008"],
        ),
        "owner-1",
    )
    await contact_lookup.run_contact_lookup_job(job.job_id)

    finished = contact_lookup.get_contact_lookup_job(job.job_id, "owner-1")
    assert finished is not None
    assert finished.status == "completed"
    by_phone = {row.phone: row for row in finished.results}
    # a1 got +15551110000 and +15552220000 (round-robin); both skipped, remainder included.
    assert by_phone["+15551110000"].status == "skipped"
    assert by_phone["+15551110000"].error_code == "flood_wait"
    assert by_phone["+15551110000"].retry_after_seconds == 30
    assert by_phone["+15552220000"].status == "skipped"
    # a2's numbers still resolved normally.
    assert by_phone["+15550000009"].status == "not_found"
    assert by_phone["+15550000008"].status == "not_found"


@pytest.mark.asyncio
async def test_cancel_stops_before_the_next_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(contact_lookup, "_BATCH_SIZE", 1)
    job = contact_lookup.start_contact_lookup_job(
        _request(account_ids=["a1"], phones=["+15551110000", "+15552220000"]),
        "owner-1",
    )

    async def cancelling(
        account_id: str, action: LookupContactsByPhone
    ) -> ContactLookupBatchResult:
        contact_lookup.cancel_contact_lookup_job(job.job_id, "owner-1")
        return await _fake_lookup(account_id, action)

    monkeypatch.setattr(contact_lookup, "execute_read", cancelling)
    await contact_lookup.run_contact_lookup_job(job.job_id)

    finished = contact_lookup.get_contact_lookup_job(job.job_id, "owner-1")
    assert finished is not None
    assert finished.status == "cancelled"
    assert finished.completed == 1
    assert contact_lookup._cancel_events == {}


def test_read_error_code_maps_the_whole_flood_family() -> None:
    def code(
        reason: str, kind: Literal["flood_wait", "unavailable", "other"] = "flood_wait"
    ) -> str:
        return contact_lookup._read_error_code(TelegramReadError(reason, kind=kind))

    assert code("FloodWait(30s)") == "flood_wait"
    assert code("FloodPremiumWait(60s)") == "premium_wait"
    assert code("SlowModeWait(10s)") == "slow_mode_wait"
    assert code("PeerFlood") == "peer_flood"
    assert code("unavailable: TimeoutError", "unavailable") == "unavailable"
    assert code("RPC: something", "other") == "failed"


@pytest.mark.asyncio
async def test_retry_contacts_stop_the_account_as_a_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(contact_lookup, "_BATCH_SIZE", 1)

    async def limited(account_id: str, action: LookupContactsByPhone) -> ContactLookupBatchResult:
        if account_id == "a1":
            return ContactLookupBatchResult(matches=[], unresolved=[], retry=list(action.phones))
        return await _fake_lookup(account_id, action)

    monkeypatch.setattr(contact_lookup, "execute_read", limited)
    job = contact_lookup.start_contact_lookup_job(
        _request(
            account_ids=["a1", "a2"],
            phones=["+15551110000", "+15550000009", "+15552220000"],
        ),
        "owner-1",
    )
    await contact_lookup.run_contact_lookup_job(job.job_id)

    finished = contact_lookup.get_contact_lookup_job(job.job_id, "owner-1")
    assert finished is not None
    by_phone = {row.phone: row for row in finished.results}
    # a1's deferred number and its not-yet-sent batch are skipped, never "not found".
    assert by_phone["+15551110000"].status == "skipped"
    assert by_phone["+15551110000"].error_code == "flood_wait"
    assert by_phone["+15552220000"].status == "skipped"
    assert by_phone["+15550000009"].status == "not_found"
    assert finished.completed == 3


@pytest.mark.asyncio
async def test_pause_only_between_batches_of_the_same_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(contact_lookup, "_BATCH_SIZE", 1)
    monkeypatch.setattr(contact_lookup, "execute_read", _fake_lookup)
    pauses: list[tuple[float, float]] = []

    def fake_uniform(low: float, high: float) -> float:
        pauses.append((low, high))
        return 0

    monkeypatch.setattr(contact_lookup.random, "uniform", fake_uniform)
    job = contact_lookup.start_contact_lookup_job(
        _request(
            account_ids=["a1", "a2"],
            phones=["+15550000001", "+15550000002", "+15550000003"],
            min_delay_seconds=1,
            max_delay_seconds=3,
        ),
        "owner-1",
    )
    await contact_lookup.run_contact_lookup_job(job.job_id)

    # a1 has two batches (one pause); a2's single batch owes nothing to a1's budget.
    assert pauses == [(1, 3)]
