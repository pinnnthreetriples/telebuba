"""While a pool search reads with an account, no other feature uses or takes it.

The search lends the account in ``services._account_owner`` and holds its lifecycle
lock. Every feature dispatches and claims under that lock, so each of them waits for
the search and goes on afterwards; the pickers that do not take the lock read the loan.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

import pytest
import pytest_asyncio

from core.config import settings
from schemas._warming_pool import PoolFillRequest
from schemas.telegram_actions import GetUserProfile
from services import _account_owner
from services.chat_broadcast import _runtime as broadcast_runtime
from services.chat_broadcast import _seams as broadcast_seams
from services.masstagging import _seams as masstagging_seams
from services.neurocomment import _discovery_pool, _gates
from services.neurocomment import _seams as neurocomment_seams
from services.neurocomment import _state as nc_state
from services.neuroshilling import _seams as neuroshilling_seams
from services.user_parser import _seams as parser_seams
from services.warming import _seams, pool_fill
from tests.services.warming._pool_support import FakeTelegram, seed_account

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from types import ModuleType

_REQUEST = PoolFillRequest(language="es", topics=["fútbol"])


class _Search:
    """A pool search on ``acc-x`` held open until the test lets it finish."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.task: asyncio.Task[object] | None = None

    async def hold(self, _account_id: str) -> None:
        self.started.set()
        await self.release.wait()


@pytest_asyncio.fixture
async def search(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Search]:
    nc_state.reset_for_tests()
    await seed_account("acc-x")
    telegram = FakeTelegram()
    held = _Search()
    telegram.during = held.hold
    monkeypatch.setattr(_seams, "execute_read", telegram.execute_read)
    monkeypatch.setattr(settings.warming, "pool_fill_pause_seconds", (0.0, 0.0))
    held.task = asyncio.create_task(pool_fill.find_channels(_REQUEST))
    await held.started.wait()
    yield held
    held.release.set()
    await held.task


def test_a_loan_refuses_claims_and_leaves_the_claim_beneath_alone() -> None:
    assert _account_owner.try_claim("acc", "chat_broadcast", "camp") is None
    assert _account_owner.lend("acc", "search-1")
    assert not _account_owner.lend("acc", "search-2")
    assert _account_owner.is_lent("acc")
    assert _account_owner.try_claim("acc", "neuroshilling", "other") == "channel_search"
    # The campaign still owns it: its coordinator keeps it, its release finds it.
    assert _account_owner.owner_of("acc") == "chat_broadcast"
    assert _account_owner.owners() == {"acc": "chat_broadcast"}
    _account_owner.give_back("acc", "search-2")
    assert _account_owner.is_lent("acc")
    _account_owner.give_back("acc", "search-1")
    assert not _account_owner.is_lent("acc")
    assert _account_owner.lent() == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "module",
    [
        neurocomment_seams,
        neuroshilling_seams,
        broadcast_seams,
        masstagging_seams,
        parser_seams,
    ],
    ids=["neurocomment", "neuroshilling", "chat_broadcast", "masstagging", "user_parser"],
)
async def test_every_feature_waits_for_the_search_to_send(
    search: _Search, module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[str] = []

    async def gateway(account_id: str, _action: object) -> object:
        sent.append(account_id)
        return object()

    monkeypatch.setattr(module, "_gateway_execute_read", gateway)
    other = asyncio.create_task(module.execute_read("acc-x", GetUserProfile()))
    await asyncio.sleep(0.05)
    assert sent == []

    search.release.set()
    await cast("asyncio.Task[object]", search.task)
    await other
    assert sent == ["acc-x"]


@pytest.mark.asyncio
async def test_a_campaign_claims_the_account_only_after_the_search(search: _Search) -> None:
    claim = asyncio.create_task(broadcast_runtime.claim_free("camp-1", ["acc-x"]))
    await asyncio.sleep(0.05)
    assert not claim.done()

    search.release.set()
    await cast("asyncio.Task[object]", search.task)
    assert await claim == ["acc-x"]


@pytest.mark.asyncio
async def test_commenting_and_discovery_pick_another_account(search: _Search) -> None:
    del search
    reason = _gates._account_block_reason("acc-x", "chan", 1, datetime.now(UTC), cast("Any", None))
    assert reason == "busy_channel_search"
    picks = {
        item.account_id: item.busy_reason
        for item in (await _discovery_pool.list_search_accounts()).items
    }
    assert picks["acc-x"] == "account_busy"
    assert await _discovery_pool.taken_account("camp-1", ["acc-x"]) == "acc-x"
