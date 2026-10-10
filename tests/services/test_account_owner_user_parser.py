"""A user-parser hold keeps the other runtimes off the session, and the parser off theirs.

Warming, the listener, the comment engine and discovery each refuse it. The registry gained
a fourth writer; every reader that refused the others refuses this one too, each with a
code that names who holds the account.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from core.db import configure_database, create_account, upsert_warming_state
from core.repositories.neurocomment import set_listener_account_id, set_listener_running
from schemas.accounts import AccountCreate
from schemas.neurocomment_discovery import DiscoverySearchOutcome
from schemas.user_parser import UserParserStartOutcome
from schemas.warming import WarmingStateWrite
from services import _account_owner
from services.neurocomment import _discovery_state, _gates
from services.neurocomment import _runtime as nc_runtime
from services.neurocomment import _runtime_operations as nc_operations
from services.neurocomment._discovery_pool import check_search_accounts, taken_account
from services.user_parser._pool import check_accounts
from services.warming import _exclusion

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _db(tmp_path: Path) -> None:
    configure_database(tmp_path / "telebuba.db")
    _discovery_state.reset_for_tests()


async def _account(account_id: str) -> None:
    await create_account(
        AccountCreate(account_id=account_id, label=account_id, session_name=account_id)
    )


def test_warming_refuses_an_account_the_parser_holds() -> None:
    _account_owner.try_claim("acc", "user_parser", "run-1")

    with pytest.raises(_exclusion.AccountUnavailableError) as refused:
        _exclusion.assert_not_campaign_held("acc")

    assert refused.value.code == "account_busy_user_parser"


@pytest.mark.asyncio
async def test_the_listener_refuses_an_account_the_parser_holds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _no_warming() -> list[str]:
        return []

    monkeypatch.setattr(nc_runtime, "_list_warming_account_ids", _no_warming)
    _account_owner.try_claim("acc", "user_parser", "run-1")

    with pytest.raises(nc_operations.ListenerBusyUserParserError):
        await nc_operations._refuse_if_busy("acc")


def test_the_comment_engine_skips_an_account_the_parser_holds() -> None:
    _account_owner.try_claim("acc", "user_parser", "run-1")

    reason = _gates._account_block_reason(
        "acc",
        "channel",
        1,
        datetime.now(UTC),
        None,  # ty: ignore[invalid-argument-type] - the ownership rung answers before the pool
    )

    assert reason == "busy_user_parser"


@pytest.mark.asyncio
async def test_discovery_refuses_an_account_the_parser_holds() -> None:
    await _account("acc")
    _account_owner.try_claim("acc", "user_parser", "run-1")

    assert await check_search_accounts("campaign", ["acc"]) == DiscoverySearchOutcome(
        status="account_busy", refused_account_id="acc"
    )
    assert await taken_account("campaign", ["acc"]) == "acc"


@pytest.mark.asyncio
async def test_the_parser_refuses_the_listener_warming_and_a_discovery_run() -> None:
    for account_id in ("listener", "warm", "searching"):
        await _account(account_id)
    await set_listener_account_id("listener")
    await set_listener_running(running=True)
    await upsert_warming_state(WarmingStateWrite(account_id="warm", state="active"))
    assert _discovery_state.try_reserve("campaign", frozenset({"searching"})) is None

    for account_id in ("listener", "warm", "searching"):
        assert await check_accounts([account_id]) == UserParserStartOutcome(
            status="account_busy", refused_account_id=account_id
        )
