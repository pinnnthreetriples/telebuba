"""A masstagging hold keeps the other runtimes off the session, and masstagging off theirs.

Warming, the listener, the comment engine, discovery, the chat broadcast and the user
parser each refuse an account a running masstagging campaign holds, each with a code
that names who holds it; and masstagging leaves out an account they hold.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from core.db import configure_database, create_account
from schemas.accounts import AccountCreate
from schemas.neurocomment_discovery import DiscoverySearchOutcome
from schemas.user_parser import UserParserStartOutcome
from services import _account_owner
from services.chat_broadcast._runtime import claim_free as broadcast_claim
from services.masstagging._runtime import claim_free as masstagging_claim
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
    _account_owner.reset_for_tests()


async def _account(account_id: str) -> None:
    await create_account(
        AccountCreate(account_id=account_id, label=account_id, session_name=account_id)
    )


def test_warming_refuses_an_account_masstagging_holds() -> None:
    _account_owner.try_claim("acc", "masstagging", "c1")

    with pytest.raises(_exclusion.AccountUnavailableError) as refused:
        _exclusion.assert_not_campaign_held("acc")

    assert refused.value.code == "account_busy_masstagging"


@pytest.mark.asyncio
async def test_the_listener_refuses_an_account_masstagging_holds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _no_warming() -> list[str]:
        return []

    monkeypatch.setattr(nc_runtime, "_list_warming_account_ids", _no_warming)
    _account_owner.try_claim("acc", "masstagging", "c1")

    with pytest.raises(nc_operations.ListenerBusyMasstaggingError):
        await nc_operations._refuse_if_busy("acc")


def test_the_comment_engine_skips_an_account_masstagging_holds() -> None:
    _account_owner.try_claim("acc", "masstagging", "c1")

    reason = _gates._account_block_reason(
        "acc",
        "channel",
        1,
        datetime.now(UTC),
        None,  # ty: ignore[invalid-argument-type] - the ownership rung answers before the pool
    )

    assert reason == "busy_masstagging"


@pytest.mark.asyncio
async def test_discovery_refuses_an_account_masstagging_holds() -> None:
    await _account("acc")
    _account_owner.try_claim("acc", "masstagging", "c1")

    assert await check_search_accounts("campaign", ["acc"]) == DiscoverySearchOutcome(
        status="account_busy", refused_account_id="acc"
    )
    assert await taken_account("campaign", ["acc"]) == "acc"


@pytest.mark.asyncio
async def test_the_broadcast_and_the_parser_leave_out_what_masstagging_holds() -> None:
    await _account("acc")
    _account_owner.try_claim("acc", "masstagging", "c1")

    assert await broadcast_claim("b1", ["acc"]) == []
    assert await check_accounts(["acc"]) == UserParserStartOutcome(
        status="account_busy", refused_account_id="acc"
    )


@pytest.mark.asyncio
async def test_masstagging_leaves_out_what_the_broadcast_holds() -> None:
    for account_id in ("held", "free"):
        await _account(account_id)
    _account_owner.try_claim("held", "chat_broadcast", "b1")

    assert await masstagging_claim("c1", ["held", "free"]) == ["free"]
    assert _account_owner.owner_of("free") == "masstagging"
