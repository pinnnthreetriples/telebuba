"""The survival probe under the shared budget: a postponed ask is not an attempt."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.db import create_account, fetch_account
from schemas.accounts import AccountCreate, AccountList
from schemas.spam_status import SpamStatusVerdict
from services.spam_intel import _gateway
from services.survival import _probe_loop, run_probe_pass
from tests.services.survival._support import at, iso, released_life

if TYPE_CHECKING:
    from collections.abc import Iterator

    from schemas.accounts import AccountRead
    from schemas.lifecycle import LifecycleEvent
    from schemas.spam_intel import ServedFrom


@pytest.fixture(autouse=True)
def _fresh_state() -> Iterator[None]:
    _probe_loop.reset_for_tests()
    yield
    _probe_loop.reset_for_tests()


class _Gateway:
    """The gateway as the loop sees it: scripted answers, and who asked in whose name."""

    def __init__(self) -> None:
        self.script: list[ServedFrom | None] = []
        self.calls: list[tuple[str, str | None]] = []

    async def __call__(self, account_id: str, **_kwargs: object) -> SpamStatusVerdict:
        self.calls.append((account_id, _gateway._CALLER.get()))
        served = self.script.pop(0) if self.script else "probe"
        return SpamStatusVerdict(
            account_id=account_id,
            status="unknown" if served in {"deferred", "refused"} else "clean",
            checked_at=iso(22),
            served_from=served,
        )


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> _Gateway:
    fake = _Gateway()

    async def log(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(_probe_loop, "refresh_spam_status", fake)
    monkeypatch.setattr(_probe_loop, "log_event", log)
    return fake


async def _seed(monkeypatch: pytest.MonkeyPatch, *accounts: tuple[str, str | None]) -> None:
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
async def test_the_window_probe_names_itself_to_the_gateway(
    monkeypatch: pytest.MonkeyPatch, gateway: _Gateway
) -> None:
    await _seed(monkeypatch, ("a", "p1"))
    await run_probe_pass(now=at(22))
    assert gateway.calls == [("a", "survival.window")]


@pytest.mark.asyncio
async def test_a_postponed_ask_does_not_start_the_retry_wait_or_the_proxy_spacing(
    monkeypatch: pytest.MonkeyPatch, gateway: _Gateway
) -> None:
    """The budget said later: the next pass may ask again at once, not 24 h and an hour on."""
    await _seed(monkeypatch, ("a", "p1"), ("b", "p1"))
    gateway.script = ["deferred", "probe"]
    first = await run_probe_pass(now=at(22))
    assert _probe_loop._state.attempts == {}
    assert _probe_loop._state.asked_by_proxy == {}
    second = await run_probe_pass(now=at(22.01))
    assert first is not None
    assert second is not None
    assert len(gateway.calls) == 2


@pytest.mark.asyncio
async def test_a_real_ask_still_starts_the_retry_wait_and_the_proxy_spacing(
    monkeypatch: pytest.MonkeyPatch, gateway: _Gateway
) -> None:
    await _seed(monkeypatch, ("a", "p1"))
    gateway.script = ["probe"]
    await run_probe_pass(now=at(22))
    assert set(_probe_loop._state.attempts) == {"a"}
    assert set(_probe_loop._state.asked_by_proxy) == {"p1"}


@pytest.mark.asyncio
async def test_a_refused_ask_is_remembered_so_it_is_not_retried_every_hour(
    monkeypatch: pytest.MonkeyPatch, gateway: _Gateway
) -> None:
    """No proxy, dead session: asking again in an hour would only be refused again."""
    await _seed(monkeypatch, ("a", "p1"))
    gateway.script = ["refused"]
    await run_probe_pass(now=at(22))
    assert set(_probe_loop._state.attempts) == {"a"}
    assert await run_probe_pass(now=at(22.02)) is None
    assert len(gateway.calls) == 1


@pytest.mark.asyncio
async def test_the_spam_block_recheck_names_itself_to_the_gateway(
    monkeypatch: pytest.MonkeyPatch, gateway: _Gateway
) -> None:
    from core.db import upsert_warming_state  # noqa: PLC0415
    from schemas.warming import WarmingStateWrite  # noqa: PLC0415

    await _seed(monkeypatch, ("a", "p1"))
    # A confirmed block on record, nothing else running the account, no window due yet.
    await upsert_warming_state(
        WarmingStateWrite(account_id="a", state="idle", spam_blocked_at=iso(1))
    )
    # Move the clock before the d7 window closes so only the block re-check is due.
    await run_probe_pass(now=at(3))
    assert gateway.calls == [("a", "survival.block_recheck")]
