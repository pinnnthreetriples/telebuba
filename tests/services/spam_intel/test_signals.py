"""The receiver of ``core``'s passive signals, and masstagging's PeerFlood as evidence (T11)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import get_spam_status
from core.repositories.spam_evidence import list_evidence
from core.spam_signals import drain_signals, emit_signal
from services import spam_intel
from services.spam_intel import _signals

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from tests.services.spam_intel.conftest import Clock

    MakeAccount = Callable[..., Awaitable[None]]


async def _kinds(account_id: str = "a1") -> list[str]:
    return sorted(item.kind for item in await list_evidence(account_id))


async def _report(kind: str, account_id: str = "a1", **extra: str) -> None:
    emit_signal(account_id, kind, source="session_check", **extra)  # ty: ignore[invalid-argument-type]
    await drain_signals()


@pytest.mark.asyncio
async def test_installed_once_or_twice_a_signal_from_core_is_one_row(
    make_account: MakeAccount,
) -> None:
    await make_account("a1")
    spam_intel.install_signal_sink()
    spam_intel.install_signal_sink()

    await _report("peer_flood_send", operation="send_dm")

    (row,) = await list_evidence("a1")
    assert (row.kind, row.source, row.operation, row.count) == (
        "peer_flood_send",
        "session_check",
        "send_dm",
        1,
    )
    verdict = (await spam_intel.verdicts_for(["a1"]))["a1"]
    assert (verdict.status, verdict.primary_kind) == ("limited", "peer_flood_send")


@pytest.mark.asyncio
async def test_before_the_app_registers_it_nothing_is_recorded(make_account: MakeAccount) -> None:
    await make_account("a1")

    await _report("peer_flood_send")

    assert await _kinds() == []


@pytest.mark.asyncio
async def test_a_closer_with_nothing_open_stores_nothing(make_account: MakeAccount) -> None:
    """The session check says "no freeze, not restricted" every time: never a row per check."""
    await make_account("a1")
    spam_intel.install_signal_sink()

    await _report("freeze_cleared")
    await _report("tos_cleared")

    assert await _kinds() == []
    verdict = (await spam_intel.verdicts_for(["a1"]))["a1"]
    assert (verdict.status, verdict.reason, verdict.tos_restricted) == (
        "unknown",
        "no_evidence",
        False,
    )


@pytest.mark.asyncio
async def test_a_freeze_is_closed_by_its_closer_and_only_by_it(
    make_account: MakeAccount, clock: Clock
) -> None:
    await make_account("a1")
    spam_intel.install_signal_sink()
    await _report("freeze_seen", detail="until=1800000000")
    clock.advance(hours=1)
    await _report("freeze_seen")  # seen again: the same state, one row

    assert (await spam_intel.verdicts_for(["a1"]))["a1"].status == "frozen"
    assert await _kinds() == ["freeze_seen"]

    clock.advance(hours=1)
    await _report("freeze_cleared")

    assert await _kinds() == ["freeze_cleared", "freeze_seen"]
    assert (await spam_intel.verdicts_for(["a1"]))["a1"].status != "frozen"


@pytest.mark.asyncio
async def test_a_restriction_flag_is_closed_by_its_closer_and_never_moves_the_verdict(
    make_account: MakeAccount, clock: Clock
) -> None:
    await make_account("a1")
    spam_intel.install_signal_sink()
    await _report("tos_restricted", detail="all:terms")

    verdict = (await spam_intel.verdicts_for(["a1"]))["a1"]
    assert (verdict.tos_restricted, verdict.tos_reason, verdict.status) == (
        True,
        "all:terms",
        "unknown",
    )

    clock.advance(hours=1)
    await _report("tos_cleared")

    assert (await spam_intel.verdicts_for(["a1"]))["a1"].tos_restricted is False


@pytest.mark.asyncio
async def test_a_shadow_success_is_kept_once_a_day_per_account_and_kind(
    make_account: MakeAccount, clock: Clock
) -> None:
    await make_account("a1")
    await make_account("a2")
    spam_intel.install_signal_sink()

    await _report("group_send_ok")
    clock.advance(hours=3)
    await _report("group_send_ok")  # the same day: not kept
    await _report("join_ok")  # another kind: kept
    await _report("group_send_ok", account_id="a2")  # another account: kept

    assert await _kinds("a1") == ["group_send_ok", "join_ok"]
    assert await _kinds("a2") == ["group_send_ok"]

    clock.advance(hours=22)
    await _report("group_send_ok")  # a new day

    assert [row.kind for row in await list_evidence("a1")].count("group_send_ok") == 2


@pytest.mark.asyncio
async def test_a_shadow_success_never_moves_a_verdict(
    make_account: MakeAccount, clock: Clock
) -> None:
    await make_account("a1")
    spam_intel.install_signal_sink()
    await _report("peer_flood_send")
    clock.advance(minutes=30)

    for kind in ("group_send_ok", "join_ok", "read_ok"):
        await _report(kind)

    verdict = (await spam_intel.verdicts_for(["a1"]))["a1"]
    assert (verdict.status, verdict.primary_kind, verdict.contradiction) == (
        "limited",
        "peer_flood_send",
        False,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("switch", ["enabled", "passive_signals"])
async def test_with_the_switches_off_nothing_is_recorded_and_nothing_is_read(
    make_account: MakeAccount, monkeypatch: pytest.MonkeyPatch, switch: str
) -> None:
    await make_account("a1")
    spam_intel.install_signal_sink()
    monkeypatch.setattr(settings.spam_intel, switch, False)

    for kind in ("peer_flood_send", "freeze_seen", "freeze_cleared", "group_send_ok"):
        await _report(kind)

    assert await _kinds() == []


@pytest.mark.asyncio
async def test_the_receiver_hands_the_signal_to_the_evidence_layer_unchanged(
    make_account: MakeAccount, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_account("a1")
    seen: list[tuple[object, ...]] = []

    async def spy(account_id: str, kind: str, **kwargs: object) -> None:
        seen.append((account_id, kind, kwargs))

    monkeypatch.setattr(_signals._evidence, "record_evidence", spy)

    await _signals.receive(
        "a1", "user_banned_in_channel", source="chat_gateway", operation="send_message", detail="X"
    )

    assert seen == [
        (
            "a1",
            "user_banned_in_channel",
            {"source": "chat_gateway", "operation": "send_message", "detail": "X"},
        )
    ]


@pytest.mark.asyncio
async def test_masstaggings_spamblock_keeps_its_row_and_is_also_peer_flood_evidence(
    make_account: MakeAccount, clock: Clock
) -> None:
    await make_account("a1")

    stored = await spam_intel.record_peer_flood_limited("a1", checked_at=clock.now().isoformat())

    assert (stored.status, stored.detail) == ("limited", "peer_flood")
    legacy = await get_spam_status("a1")
    assert legacy is not None
    assert (legacy.status, legacy.detail) == ("limited", "peer_flood")
    (row,) = await list_evidence("a1")
    assert (row.kind, row.source, row.operation) == ("peer_flood_send", "masstagging", "masstag")
    verdict = (await spam_intel.verdicts_for(["a1"]))["a1"]
    assert (verdict.status, verdict.primary_kind) == ("limited", "peer_flood_send")


@pytest.mark.asyncio
async def test_masstaggings_write_is_unchanged_when_passive_signals_are_off(
    make_account: MakeAccount, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_account("a1")
    monkeypatch.setattr(settings.spam_intel, "passive_signals", False)

    await spam_intel.record_peer_flood_limited("a1", checked_at=clock.now().isoformat())

    legacy = await get_spam_status("a1")
    assert legacy is not None
    assert (legacy.status, legacy.detail) == ("limited", "peer_flood")
    assert await _kinds() == []
