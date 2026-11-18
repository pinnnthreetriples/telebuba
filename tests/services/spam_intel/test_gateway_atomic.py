"""Admission and the ledger row are ONE step: concurrent asks cannot both read one snapshot.

The budget (design 5.5) is computed from the ledger, so two accounts that decide at the same
moment must not both be admitted by a snapshot that does not yet hold the other's attempt:
the proxy spacing and the fleet hard cap would be crossed by exactly the burst they exist for.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any

import pytest

from core.config import settings
from core.db import _get_engine
from schemas.spam_status import SpamBotHistory
from services import spam_intel
from services.spam_intel import _gateway, _history

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from schemas.spam_intel import BudgetSnapshot
    from tests.services.spam_intel.conftest import FakeProbe

    MakeAccount = Callable[..., Awaitable[None]]


def _rows(where: str = "1 = 1") -> list[tuple[str, str, str | None]]:
    with _get_engine().connect() as connection:
        return [
            (str(r[0]), str(r[1]), r[2])
            for r in connection.exec_driver_sql(
                f"SELECT account_id, kind, proxy_key FROM spam_probe_attempts WHERE {where}"  # noqa: S608
            )
        ]


@pytest.mark.asyncio
async def test_two_accounts_on_one_proxy_cannot_both_be_admitted_together(
    make_account: MakeAccount, probe: FakeProbe, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.spam_intel, "proxy_spacing_minutes", 120.0)
    await make_account("acc-1", proxy="shared")
    await make_account("acc-2", proxy="shared")
    probe.delay = 0.02
    answers = await asyncio.gather(
        spam_intel.ask("acc-1", force=True), spam_intel.ask("acc-2", force=True)
    )
    assert len(probe.calls) == 1
    assert sorted(a.served_from or "" for a in answers) == ["deferred", "probe"]
    assert {a.reason for a in answers} == {None, "proxy_spacing"}
    assert len(_rows()) == 1


@pytest.mark.asyncio
async def test_a_burst_across_the_fleet_cannot_pass_the_hard_cap(
    make_account: MakeAccount, probe: FakeProbe, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.spam_intel, "fleet_soft_cap", 1)
    monkeypatch.setattr(settings.spam_intel, "fleet_hard_cap", 3)
    for index in range(8):
        await make_account(f"acc-{index}")
    probe.delay = 0.02
    answers = await asyncio.gather(*(spam_intel.ask(f"acc-{i}", force=True) for i in range(8)))
    assert len(probe.calls) == 3
    assert len(_rows("kind = 'probe'")) == 3
    assert sorted(a.served_from or "" for a in answers) == ["deferred"] * 5 + ["probe"] * 3
    assert {a.reason for a in answers if a.served_from == "deferred"} == {"fleet_hard_cap"}


@pytest.mark.asyncio
async def test_the_fleet_gap_holds_against_a_simultaneous_burst(
    make_account: MakeAccount, probe: FakeProbe, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.spam_intel, "fleet_gap_min_seconds", 180.0)
    monkeypatch.setattr(settings.spam_intel, "fleet_gap_max_seconds", 180.0)
    for index in range(5):
        await make_account(f"acc-{index}")
    await asyncio.gather(*(spam_intel.ask(f"acc-{i}", force=True) for i in range(5)))
    assert len(probe.calls) == 1


class _Reader:
    """Stands in for ``read_spambot_history``."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, account_id: str, _limit: int) -> SpamBotHistory:
        self.calls.append(account_id)
        return SpamBotHistory(account_id=account_id, texts=[])


def _rendezvous(real: Callable[..., Awaitable[BudgetSnapshot]]) -> Callable[..., Any]:
    """Make two snapshot readers meet: each waits (briefly) for the other to have read."""
    arrived = {"n": 0}
    both = asyncio.Event()

    async def _load(*args: Any, **kwargs: Any) -> BudgetSnapshot:
        snapshot = await real(*args, **kwargs)
        arrived["n"] += 1
        if arrived["n"] >= 2:
            both.set()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(both.wait(), timeout=0.3)
        return snapshot

    return _load


@pytest.mark.asyncio
async def test_a_history_read_and_a_probe_behind_one_proxy_are_admitted_one_at_a_time(
    make_account: MakeAccount, probe: FakeProbe, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Forced to read ONE snapshot (the worst interleaving), only one of them may go on."""
    monkeypatch.setattr(settings.spam_intel, "proxy_spacing_minutes", 120.0)
    reader = _Reader()
    monkeypatch.setattr(_history, "read_spambot_history", reader)

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(_history, "_sleep", _no_sleep)
    meeting = _rendezvous(_gateway.load_budget_snapshot)
    monkeypatch.setattr(_gateway, "load_budget_snapshot", meeting)
    monkeypatch.setattr(_history, "load_budget_snapshot", meeting)
    await make_account("acc-1", proxy="shared")
    await make_account("acc-2", proxy="shared")
    probe.delay = 0.02
    await asyncio.gather(spam_intel.ask("acc-2", force=True), _history.run_history_sweep())
    assert len(probe.calls) + len(reader.calls) == 1
    assert len(_rows()) == 1


@pytest.mark.asyncio
async def test_the_admission_gate_is_not_held_while_a_probe_waits_on_the_network(
    make_account: MakeAccount, probe: FakeProbe
) -> None:
    """A slow probe of one account must not stop the budget deciding for another."""
    await make_account("slow")
    await make_account("quick")
    probe.delay = 0.3
    slow = asyncio.create_task(spam_intel.ask("slow", force=True))
    await asyncio.sleep(0.05)  # "slow" is now waiting on its reply, its row reserved
    probe.delay = 0.0
    answer = await asyncio.wait_for(spam_intel.ask("quick", force=True), timeout=0.2)
    assert answer.served_from == "probe"
    assert not slow.done()
    await slow
