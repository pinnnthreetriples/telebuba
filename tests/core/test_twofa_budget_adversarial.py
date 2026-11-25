"""Adversarial checks of the persisted budget of wrong cloud-password answers (real temp SQLite)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from telethon import errors

from core import twofa_budget
from core.config import settings
from core.db import (
    confirm_twofa_password_used,
    create_account,
    promote_account_twofa_pending,
    set_account_twofa_current_only,
    set_account_twofa_password,
    set_account_twofa_pending,
)
from core.repositories._accounts_twofa import (
    mark_account_twofa_pending_refused,
    record_twofa_rejection,
    remaining_twofa_attempts,
    reset_twofa_rejections,
    twofa_password_candidates,
)
from core.telegram_client._twofa_candidates import sign_in_with_passwords
from core.twofa_budget import TwofaAttemptsExhaustedError, record_password_rejection
from schemas.accounts import AccountCreate
from schemas.phone_login import PhoneCodeSubmit

_T0 = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
_WINDOW = 24.0
_MAX = 5
_PW = "stale-Pa55word!"


async def _open(account_id: str = "a") -> None:
    await create_account(AccountCreate(account_id=account_id))


async def _reject(now: datetime, account_id: str = "a") -> int | None:
    return await record_twofa_rejection(account_id, window_hours=_WINDOW, now=now)


async def _left(now: datetime, account_id: str = "a") -> int:
    return await remaining_twofa_attempts(
        account_id, max_rejections=_MAX, window_hours=_WINDOW, now=now
    )


class _Client:
    def __init__(self, good: str | None = None) -> None:
        self.good = good
        self.sent: list[str] = []

    async def sign_in(self, *, password: str | None = None, **_code: object) -> None:
        assert password is not None
        self.sent.append(password)
        if password != self.good:
            raise errors.PasswordHashInvalidError(request=None)


@pytest.fixture(autouse=True)
def pinned(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    monkeypatch.setattr(settings.session_audit, "wrong_answer_limit", _MAX)
    monkeypatch.setattr(settings.session_audit, "wrong_answer_window_hours", _WINDOW)
    rows: list[tuple[str, dict[str, Any]]] = []

    async def _log(_level: str, event: str, **kwargs: Any) -> None:
        rows.append((event, kwargs))

    monkeypatch.setattr(twofa_budget, "log_event", _log)
    return rows


# ------------------------------------------------------------------ window boundary


@pytest.mark.asyncio
async def test_window_rollover_is_consistent_between_record_and_remaining() -> None:
    await _open()
    await _reject(_T0)
    exactly = _T0 + timedelta(hours=_WINDOW)
    just_after = exactly + timedelta(microseconds=1)
    assert await _left(exactly) == _MAX - 1  # still the same window at the boundary
    assert await _left(just_after) == _MAX  # one tick later it is a new one
    assert await _reject(just_after) == 1  # and record agrees: restarts at 1
    assert await _left(just_after + timedelta(hours=1)) == _MAX - 1


@pytest.mark.asyncio
async def test_boundary_with_and_without_fraction_seconds_orders_correctly() -> None:
    """``isoformat`` drops ``.000000``; ``+`` sorts before ``.`` and must not flip the result."""
    await _open()
    whole = datetime(2026, 11, 25, 12, 0, 0, tzinfo=UTC)
    await _reject(whole)
    assert (
        await _left(whole + timedelta(hours=23, minutes=59, seconds=59, microseconds=500000)) == 4
    )
    assert await _left(whole + timedelta(hours=24, microseconds=500000)) == _MAX


@pytest.mark.asyncio
async def test_a_blocked_attempt_does_not_extend_the_window() -> None:
    await _open()
    for index in range(_MAX):
        await _reject(_T0 + timedelta(hours=index))
    assert await _left(_T0 + timedelta(hours=10)) == 0
    # operator-typed rejections keep counting but the window still ends 24h after the first
    await _reject(_T0 + timedelta(hours=20))
    assert await _left(_T0 + timedelta(hours=24, minutes=1)) == _MAX


@pytest.mark.asyncio
async def test_a_clock_that_ran_ahead_cannot_withhold_the_password_for_years() -> None:
    await _open()
    future = _T0 + timedelta(days=3 * 365)
    for _ in range(_MAX):
        await _reject(future)
    assert await _left(_T0 + timedelta(days=1)) == _MAX


# ---------------------------------------------------------------------- concurrency


@pytest.mark.asyncio
async def test_concurrent_rejections_lose_no_update() -> None:
    await _open()
    counts = await asyncio.gather(*(_reject(_T0) for _ in range(40)))
    assert sorted(c for c in counts if c is not None) == list(range(1, 41))
    assert await _left(_T0) == 0


@pytest.mark.asyncio
async def test_concurrent_rejections_of_two_accounts_do_not_mix() -> None:
    await _open("a")
    await _open("b")
    await asyncio.gather(
        *(_reject(_T0, "a") for _ in range(7)), *(_reject(_T0, "b") for _ in range(2))
    )
    assert await _left(_T0, "a") == 0
    assert await _left(_T0, "b") == _MAX - 2


@pytest.mark.asyncio
async def test_exhaustion_is_journalled_exactly_once_under_concurrency(
    pinned: list[tuple[str, dict[str, Any]]],
) -> None:
    await _open()
    await asyncio.gather(*(record_password_rejection("a") for _ in range(20)))
    assert [event for event, _ in pinned].count("twofa_attempts_exhausted") == 1


# ----------------------------------------------------------------- unknown account


@pytest.mark.asyncio
async def test_unknown_account_is_inert(pinned: list[tuple[str, dict[str, Any]]]) -> None:
    assert await _reject(_T0, "ghost") is None
    assert await _left(_T0, "ghost") == _MAX
    await record_password_rejection("ghost")
    await twofa_budget.ensure_twofa_budget("ghost")
    assert await reset_twofa_rejections("ghost") is False
    assert await confirm_twofa_password_used("ghost", "x") is False
    assert pinned == []


# ------------------------------------------------------------------------- resets


async def _spend() -> None:
    for _ in range(_MAX):
        await _reject(datetime.now(UTC))


async def _exhausted() -> bool:
    return await remaining_twofa_attempts("a", max_rejections=_MAX, window_hours=_WINDOW) == 0


@pytest.mark.asyncio
async def test_operator_replacing_the_password_resets_and_stored_is_sent_again() -> None:
    await _open()
    await set_account_twofa_password("a", _PW)
    await _spend()
    assert await _exhausted()
    with pytest.raises(TwofaAttemptsExhaustedError):
        await sign_in_with_passwords(_Client(), "a", [_PW])  # ty: ignore[invalid-argument-type]
    # the operator fixes the stored password (same string or different): a fresh budget
    await set_account_twofa_password("a", "correct-pw")
    assert not await _exhausted()
    client = _Client(good="correct-pw")
    assert await sign_in_with_passwords(client, "a", ["correct-pw"]) == "correct-pw"  # ty: ignore[invalid-argument-type]
    assert client.sent == ["correct-pw"]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["current_only", "promote", "confirm", "clear", "reset"])
async def test_every_reset_path_starts_a_fresh_budget(path: str) -> None:
    await _open()
    await set_account_twofa_password("a", "old")
    await set_account_twofa_pending("a", "new")
    await _spend()
    assert await _exhausted()
    if path == "current_only":
        await set_account_twofa_current_only("a", "new")
    elif path == "promote":
        await promote_account_twofa_pending("a", "new")
    elif path == "confirm":
        await confirm_twofa_password_used("a", "new")
    elif path == "clear":
        await set_account_twofa_password("a", None)
    else:
        await reset_twofa_rejections("a")
    assert not await _exhausted()


@pytest.mark.asyncio
async def test_a_good_password_resets_even_when_it_is_the_typed_one() -> None:
    await _open()
    await set_account_twofa_password("a", "old")
    await _spend()
    client = _Client(good="typed")
    assert await sign_in_with_passwords(client, "a", ["typed"], typed="typed") == "typed"  # ty: ignore[invalid-argument-type]
    assert not await _exhausted()


# ------------------------------------------------------------- the typed password


@pytest.mark.asyncio
async def test_typed_password_is_sent_when_spent_and_counted_never_blocked() -> None:
    await _open()
    await set_account_twofa_password("a", "old")
    await _spend()
    client = _Client()
    for _ in range(3):
        with pytest.raises(errors.PasswordHashInvalidError):
            await sign_in_with_passwords(client, "a", ["junk"], typed="junk")  # ty: ignore[invalid-argument-type]
    assert client.sent == ["junk"] * 3


@pytest.mark.asyncio
async def test_typed_flag_only_covers_the_typed_string_not_stored_fallbacks() -> None:
    await _open()
    await set_account_twofa_password("a", "stored")
    await _spend()
    client = _Client()
    with pytest.raises(TwofaAttemptsExhaustedError):
        await sign_in_with_passwords(client, "a", ["typed", "stored"], typed="typed")  # ty: ignore[invalid-argument-type]
    assert client.sent == ["typed"]


def test_password_typed_cannot_be_set_by_an_api_client() -> None:
    from schemas.phone_login import SubmitCodeRequest  # noqa: PLC0415

    assert "password_typed" not in SubmitCodeRequest.model_fields
    request = SubmitCodeRequest.model_validate(
        {"code": "1", "password": "x", "password_typed": True}
    )
    assert not hasattr(request, "password_typed")
    assert PhoneCodeSubmit.model_fields["password_typed"].default is False


@pytest.mark.asyncio
async def test_the_last_budget_unit_does_not_withhold_a_correct_pending_candidate() -> None:
    await _open()
    await set_account_twofa_password("a", "stale")
    await set_account_twofa_pending("a", "fresh")
    for _ in range(_MAX - 1):
        await _reject(datetime.now(UTC))
    client = _Client(good="fresh")
    assert await sign_in_with_passwords(client, "a", ["stale", "fresh"]) == "fresh"  # ty: ignore[invalid-argument-type]


# ------------------------------------------------------------------- candidates


@pytest.mark.asyncio
async def test_candidates_ordering_dedup_and_refused_pending_exclusion() -> None:
    await _open()
    assert await twofa_password_candidates("a") == []
    await set_account_twofa_password("a", "cur")
    await set_account_twofa_pending("a", "pen")
    assert await twofa_password_candidates("a") == ["cur", "pen"]
    await mark_account_twofa_pending_refused("a", "pen")
    assert await twofa_password_candidates("a") == ["cur"]
    await set_account_twofa_pending("a", "pen2")  # a new candidate clears the refusal
    assert await twofa_password_candidates("a") == ["cur", "pen2"]
    await set_account_twofa_pending("a", "cur")
    assert list(dict.fromkeys(await twofa_password_candidates("a"))) == ["cur"]
    assert await twofa_password_candidates("ghost") == []


@pytest.mark.asyncio
async def test_only_a_pending_candidate_that_was_refused_leaves_nothing_to_send() -> None:
    await _open()
    await set_account_twofa_pending("a", "pen")
    await mark_account_twofa_pending_refused("a", "pen")
    assert await twofa_password_candidates("a") == []


# ------------------------------------------------------------- no password leaks


@pytest.mark.asyncio
async def test_a_failed_budget_write_journals_only_the_error_type(
    monkeypatch: pytest.MonkeyPatch, pinned: list[tuple[str, dict[str, Any]]]
) -> None:
    async def explode(*_a: object, **_k: object) -> int:
        message = f"database said: {_PW}"
        raise RuntimeError(message)

    monkeypatch.setattr(twofa_budget, "record_twofa_rejection", explode)
    await record_password_rejection("a")
    assert [event for event, _ in pinned] == ["twofa_budget_write_failed"]
    assert _PW not in repr(pinned)


@pytest.mark.asyncio
async def test_exhaustion_error_and_sender_exceptions_carry_no_password() -> None:
    await _open()
    await set_account_twofa_password("a", _PW)
    await _spend()
    with pytest.raises(TwofaAttemptsExhaustedError) as caught:
        await sign_in_with_passwords(_Client(), "a", [_PW])  # ty: ignore[invalid-argument-type]
    assert _PW not in repr(caught.value)
    assert _PW not in str(caught.value)
    assert caught.value.args == ()
