"""The watch: one account a pass, jittered intervals, proxies spaced, the dead sent to a check."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from schemas.accounts import AccountRead
from schemas.passkey import AccountPasskeyCheck
from services import _account_owner
from services.accounts import AccountActionError
from services.session_guard import _watch

if TYPE_CHECKING:
    from collections.abc import Iterator

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


def _account(account_id: str, *, proxy: str | None = "p1", status: str = "alive") -> AccountRead:
    return AccountRead.model_validate(
        {
            "account_id": account_id,
            "status": status,
            "proxy_id": proxy,
            "created_at": "2026-10-01T00:00:00+00:00",
            "updated_at": "2026-10-01T00:00:00+00:00",
        }
    )


class _Wire:
    def __init__(self) -> None:
        self.accounts: list[AccountRead] = []
        self.audited: dict[str, str] = {}
        self.reads: list[str] = []
        self.refusal: dict[str, str] = {}
        self.checked: list[str] = []
        self.passkeys: set[str] = set()
        self.passkey_state = "present"
        self.events: list[tuple[str, str, str | None]] = []


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Wire]:
    state = _Wire()
    _watch.reset_for_tests()
    _account_owner.reset_for_tests()
    monkeypatch.setattr(_watch.settings.session_audit, "watch_startup_delay_seconds", 0.0)
    monkeypatch.setattr(_watch, "_rng", random.Random(7))  # noqa: S311 - a seeded test draw

    async def _list() -> SimpleNamespace:
        return SimpleNamespace(accounts=state.accounts)

    async def _times() -> dict[str, str]:
        return state.audited

    async def _audit(account_id: str) -> None:
        state.reads.append(account_id)
        if account_id in state.refusal:
            raise AccountActionError(state.refusal[account_id])

    async def _check(request: object) -> None:
        state.checked.append(request.account_id)  # ty: ignore[unresolved-attribute]

    async def _has_passkey(account_id: str) -> object | None:
        return object() if account_id in state.passkeys else None

    async def _passkey(account_id: str) -> AccountPasskeyCheck:
        return AccountPasskeyCheck(account_id=account_id, telegram_state=state.passkey_state)  # ty: ignore[invalid-argument-type]

    async def _log(level: str, event: str, **kwargs: object) -> None:
        state.events.append((level, event, str(kwargs.get("account_id"))))

    monkeypatch.setattr(_watch, "list_accounts", _list)
    monkeypatch.setattr(_watch, "list_session_audit_times", _times)
    monkeypatch.setattr(_watch, "audit_account_sessions", _audit)
    monkeypatch.setattr(_watch, "check_account_session", _check)
    monkeypatch.setattr(_watch, "fetch_account_passkey", _has_passkey)
    monkeypatch.setattr(_watch, "check_account_passkey", _passkey)
    monkeypatch.setattr(_watch, "log_event", _log)
    yield state
    _account_owner.reset_for_tests()


@pytest.mark.asyncio
async def test_an_account_never_audited_is_read_at_once_and_only_one_per_pass(wire: _Wire) -> None:
    wire.accounts = [_account("a", proxy="p1"), _account("b", proxy="p2")]

    first = await _watch.run_watch_pass(now=NOW)
    second = await _watch.run_watch_pass(now=NOW)

    assert (first, second) == ("a", "b")
    assert wire.reads == ["a", "b"]


@pytest.mark.asyncio
async def test_an_account_read_just_now_waits_a_drawn_interval_within_the_range(
    wire: _Wire,
) -> None:
    wire.accounts = [_account("a")]
    await _watch.run_watch_pass(now=NOW)

    assert await _watch.run_watch_pass(now=NOW + timedelta(hours=5, minutes=59)) is None
    due = _watch._state.due["a"] - NOW
    assert timedelta(hours=6) <= due <= timedelta(hours=12)
    assert await _watch.run_watch_pass(now=NOW + timedelta(hours=12, minutes=1)) == "a"


@pytest.mark.asyncio
async def test_each_account_gets_its_own_draw_so_a_fleet_never_reads_in_step(wire: _Wire) -> None:
    wire.accounts = [_account(f"acc-{n}", proxy=f"p{n}") for n in range(6)]

    for _ in range(6):
        await _watch.run_watch_pass(now=NOW)

    dues = {_watch._state.due[a.account_id] for a in wire.accounts}
    assert len(dues) > 1


@pytest.mark.asyncio
async def test_a_proxy_is_not_read_twice_within_the_spacing(wire: _Wire) -> None:
    wire.accounts = [_account("a", proxy="p1"), _account("b", proxy="p1")]

    first = await _watch.run_watch_pass(now=NOW)
    soon = await _watch.run_watch_pass(now=NOW + timedelta(minutes=5))
    later = await _watch.run_watch_pass(now=NOW + timedelta(hours=1))

    assert (first, soon, later) == ("a", None, "b")


@pytest.mark.asyncio
async def test_only_alive_free_accounts_behind_a_proxy_are_read(wire: _Wire) -> None:
    wire.accounts = [
        _account("dead", status="unauthorized"),
        _account("noproxy", proxy=None),
        _account("busy"),
        _account("ok", proxy="p9"),
    ]
    _account_owner.try_claim("busy", "warming", "run-1")

    assert await _watch.run_watch_pass(now=NOW) == "ok"
    assert await _watch.run_watch_pass(now=NOW) is None
    assert wire.reads == ["ok"]


@pytest.mark.asyncio
async def test_an_account_audited_before_the_restart_is_not_read_again_at_once(wire: _Wire) -> None:
    wire.accounts = [_account("a")]
    wire.audited = {"a": (NOW - timedelta(minutes=30)).isoformat()}

    assert await _watch.run_watch_pass(now=NOW) is None


@pytest.mark.asyncio
async def test_the_first_pass_waits_after_startup(
    wire: _Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    wire.accounts = [_account("a")]
    monkeypatch.setattr(_watch.settings.session_audit, "watch_startup_delay_seconds", 3600.0)

    assert await _watch.run_watch_pass(now=NOW) is None
    assert wire.reads == []


@pytest.mark.asyncio
async def test_the_watch_can_be_switched_off(wire: _Wire, monkeypatch: pytest.MonkeyPatch) -> None:
    wire.accounts = [_account("a")]
    monkeypatch.setattr(_watch.settings.session_audit, "watch_enabled", False)

    assert await _watch.run_watch_pass(now=NOW) is None


@pytest.mark.asyncio
async def test_a_refused_read_is_retried_after_the_short_wait_not_a_full_interval(
    wire: _Wire,
) -> None:
    wire.accounts = [_account("a")]
    wire.refusal = {"a": "unavailable"}

    assert await _watch.run_watch_pass(now=NOW) == "a"

    assert _watch._state.due["a"] == NOW + timedelta(hours=1)
    assert wire.checked == []
    assert wire.events == []  # the audit logged its own refusal


@pytest.mark.asyncio
async def test_a_dead_session_goes_through_the_session_check_that_records_it(wire: _Wire) -> None:
    wire.accounts = [_account("a")]
    wire.refusal = {"a": "session_dead"}

    await _watch.run_watch_pass(now=NOW)

    assert wire.checked == ["a"]


@pytest.mark.asyncio
async def test_a_stored_passkey_that_telegram_no_longer_lists_is_reported(wire: _Wire) -> None:
    wire.accounts = [_account("a")]
    wire.passkeys = {"a"}
    wire.passkey_state = "missing"

    await _watch.run_watch_pass(now=NOW)

    assert wire.events == [("WARNING", "session_watch_passkey_missing", "a")]


@pytest.mark.parametrize("state", ["present", "unknown"])
@pytest.mark.asyncio
async def test_a_passkey_that_is_listed_or_unknown_is_not_news(wire: _Wire, state: str) -> None:
    wire.accounts = [_account("a")]
    wire.passkeys = {"a"}
    wire.passkey_state = state

    await _watch.run_watch_pass(now=NOW)

    assert wire.events == []


@pytest.mark.asyncio
async def test_an_account_without_a_passkey_is_not_asked(wire: _Wire) -> None:
    wire.accounts = [_account("a")]
    wire.passkey_state = "missing"

    await _watch.run_watch_pass(now=NOW)

    assert wire.events == []
