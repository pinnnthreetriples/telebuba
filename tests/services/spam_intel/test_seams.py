"""The domain seams reach the gateway under their own holder name, with their old signature."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from services import _account_owner, spam_intel
from services.neurocomment import _seams as neurocomment_seams
from services.warming import _seams as warming_seams

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from tests.services.spam_intel.conftest import FakeProbe

    MakeAccount = Callable[..., Awaitable[None]]


@pytest.mark.asyncio
async def test_warming_asks_for_an_account_it_holds_itself(make_account: MakeAccount) -> None:
    await make_account("acc-1")
    assert _account_owner.try_claim("acc-1", "warming", "run-1") is None
    verdict = await warming_seams._refresh_spam_status("acc-1", force=True)
    assert verdict.served_from == "probe"


@pytest.mark.asyncio
async def test_warming_waits_for_an_account_a_campaign_holds(
    make_account: MakeAccount, probe: FakeProbe
) -> None:
    await make_account("acc-1")
    assert _account_owner.try_claim("acc-1", "chat_broadcast", "run-1") is None
    verdict = await warming_seams._refresh_spam_status("acc-1", force=True)
    assert (verdict.served_from, verdict.reason) == ("deferred", "busy")
    assert probe.calls == []


@pytest.mark.asyncio
async def test_neurocomment_holds_nothing_so_it_waits_for_any_owner(
    make_account: MakeAccount,
) -> None:
    await make_account("acc-1")
    assert _account_owner.try_claim("acc-1", "warming", "run-1") is None
    verdict = await neurocomment_seams._refresh_spam_status("acc-1", force=True)
    assert verdict.reason == "busy"


@pytest.mark.asyncio
async def test_nested_scopes_restore_the_outer_name() -> None:
    from services.spam_intel import _gateway  # noqa: PLC0415

    with spam_intel.caller_scope("outer"):
        with spam_intel.caller_scope("inner"):
            assert _gateway._CALLER.get() == "inner"
        assert _gateway._CALLER.get() == "outer"
    assert _gateway._CALLER.get() is None


def test_a_deferred_answer_carries_a_parsable_retry_time() -> None:
    from schemas.spam_status import SpamStatusVerdict  # noqa: PLC0415

    verdict = SpamStatusVerdict(
        account_id="a",
        status="unknown",
        checked_at="2026-11-16T12:00:00+00:00",
        served_from="deferred",
        retry_at="2026-11-16T14:00:00+00:00",
    )
    retry = spam_intel.parse_retry(verdict)
    assert retry is not None
    assert retry.hour == 14
    assert spam_intel.parse_retry(verdict.model_copy(update={"retry_at": None})) is None


@pytest.mark.asyncio
async def test_masstagging_s_spamblock_goes_through_the_gateway(make_account: MakeAccount) -> None:
    from core.db import get_spam_status  # noqa: PLC0415

    await make_account("acc-1")
    saved = await spam_intel.record_peer_flood_limited(
        "acc-1", checked_at="2026-11-16T12:00:00+00:00"
    )
    stored = await get_spam_status("acc-1")
    assert stored is not None
    assert stored == saved
    assert (stored.status, stored.detail) == ("limited", "peer_flood")
