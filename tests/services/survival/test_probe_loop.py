"""The hourly survival probe: one cached @SpamBot ask per pass, spaced per proxy."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import create_account, fetch_account
from schemas.accounts import AccountCreate, AccountList
from schemas.spam_status import SpamStatusVerdict
from services.survival import _probe_loop, run_probe_pass
from tests.services.survival._support import at, iso, released_life

if TYPE_CHECKING:
    from collections.abc import Iterator

    from schemas.accounts import AccountRead
    from schemas.lifecycle import LifecycleEvent


@pytest.fixture(autouse=True)
def _fresh_state() -> Iterator[None]:
    _probe_loop.reset_for_tests()
    yield
    _probe_loop.reset_for_tests()


class _Asks:
    def __init__(self) -> None:
        self.account_ids: list[str] = []
        self.logged: list[tuple[str, dict[str, object]]] = []


@pytest.fixture
def asks(monkeypatch: pytest.MonkeyPatch) -> _Asks:
    seen = _Asks()

    async def refresh(account_id: str, *, force: bool = False) -> SpamStatusVerdict:
        assert not force, "the probe must reuse a fresh cached answer"
        seen.account_ids.append(account_id)
        return SpamStatusVerdict(account_id=account_id, status="clean", checked_at=iso(22))

    async def log(
        _level: str,
        event: str,
        account_id: str | None = None,  # noqa: ARG001 - mirrors log_event
        extra: dict[str, object] | None = None,
    ) -> None:
        seen.logged.append((event, extra or {}))

    monkeypatch.setattr(_probe_loop, "refresh_spam_status", refresh)
    monkeypatch.setattr(_probe_loop, "log_event", log)
    return seen


async def _seed(monkeypatch: pytest.MonkeyPatch, *accounts: tuple[str, str | None]) -> None:
    """Released accounts on day 14, born the day before the synthetic journal starts."""
    rows: list[AccountRead] = []
    events: list[LifecycleEvent] = []
    for account_id, proxy_id in accounts:
        await create_account(AccountCreate(account_id=account_id))
        account = await fetch_account(account_id)
        assert account is not None
        rows.append(account.model_copy(update={"created_at": iso(-1), "proxy_id": proxy_id}))
        events.extend(released_life(14, account=account_id))

    async def listed() -> AccountList:
        return AccountList(accounts=rows)

    async def journal(account_ids: list[str] | None = None) -> list[LifecycleEvent]:
        return [event for event in events if account_ids is None or event.account_id in account_ids]

    monkeypatch.setattr(_probe_loop, "list_accounts", listed)
    monkeypatch.setattr(_probe_loop, "list_all_lifecycle_events", journal)


@pytest.mark.asyncio
async def test_one_pass_asks_about_one_account(
    monkeypatch: pytest.MonkeyPatch, asks: _Asks
) -> None:
    await _seed(monkeypatch, ("a", "p1"), ("b", "p2"))

    first = await run_probe_pass(now=at(22))

    assert first in {"a", "b"}
    assert asks.account_ids == [first]
    assert asks.logged == [("survival_probe", {"window": "d7", "status": "clean"})]


@pytest.mark.asyncio
async def test_accounts_behind_one_proxy_wait_an_hour(
    monkeypatch: pytest.MonkeyPatch, asks: _Asks
) -> None:
    await _seed(monkeypatch, ("a", "p1"), ("b", "p1"), ("c", None))

    await run_probe_pass(now=at(22))
    await run_probe_pass(now=at(22.01))
    await run_probe_pass(now=at(22.02))

    # One behind p1, one direct; the second p1 account waits out the spacing.
    assert len(asks.account_ids) == 2
    assert "c" in asks.account_ids


@pytest.mark.asyncio
async def test_a_pass_journals_a_death_no_session_check_saw(
    monkeypatch: pytest.MonkeyPatch, asks: _Asks
) -> None:
    """An action marked the account frozen; the counter must still see it die — once."""
    await _seed(monkeypatch, ("a", "p1"))
    recorded: list[tuple[str, str]] = []

    async def record(account: AccountRead, status: str) -> None:
        recorded.append((account.account_id, status))

    monkeypatch.setattr(_probe_loop, "record_session_death", record)
    frozen = (await _probe_loop.list_accounts()).accounts[0].model_copy(update={"status": "frozen"})

    async def listed() -> AccountList:
        return AccountList(accounts=[frozen])

    monkeypatch.setattr(_probe_loop, "list_accounts", listed)

    assert await run_probe_pass(now=at(22)) is None
    assert recorded == [("a", "frozen")]
    assert asks.account_ids == [], "a dead account is never asked"


@pytest.mark.asyncio
async def test_switched_off_probes_ask_nothing(
    monkeypatch: pytest.MonkeyPatch, asks: _Asks
) -> None:
    await _seed(monkeypatch, ("a", "p1"))
    monkeypatch.setattr(settings.survival, "probes_enabled", False)

    assert await run_probe_pass(now=at(22)) is None
    assert asks.account_ids == []


@pytest.mark.asyncio
async def test_the_loop_survives_a_failed_pass(
    monkeypatch: pytest.MonkeyPatch, asks: _Asks
) -> None:
    passes: list[int] = []
    second = asyncio.Event()

    async def flaky() -> None:
        passes.append(1)
        if len(passes) == 1:
            msg = "db locked"
            raise RuntimeError(msg)
        second.set()

    monkeypatch.setattr(_probe_loop, "run_probe_pass", flaky)
    monkeypatch.setattr(settings.survival, "probe_startup_delay_seconds", 0.0)
    monkeypatch.setattr(settings.survival, "probe_interval_seconds", 0.001)

    await _probe_loop.start_survival_probes()
    await _probe_loop.start_survival_probes()  # a second start is a no-op
    await asyncio.wait_for(second.wait(), timeout=5)
    await _probe_loop.shutdown_survival_probes()
    await _probe_loop.shutdown_survival_probes()  # and so is a second shutdown

    assert asks.logged[0] == ("survival_probe_failed", {"error_type": "RuntimeError"})


@pytest.mark.asyncio
async def test_switched_off_probes_start_no_task(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.survival, "probes_enabled", False)

    await _probe_loop.start_survival_probes()

    assert _probe_loop._state.task is None
