"""DM peer resolution: the phone plumbing, and skipping partners we can't address."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.db import (
    load_warming_settings,
    oldest_unreplied_for,
    record_dialogue_message,
)
from schemas.gemini import GeminiResult
from schemas.telegram_actions import ActionResult, TelegramAction
from services.warming import _seams
from tests.services.warming._support import _account, _Recorder

if TYPE_CHECKING:
    from schemas.accounts import AccountRead


async def _unresolvable(account_id: str, action: TelegramAction) -> ActionResult:
    """Stand-in gateway where every DM peer is permanently unaddressable."""
    return ActionResult(
        status="failed",
        action_type=action.action_type,
        account_id=account_id,
        error_type="DmPeerUnresolvedError",
    )


def _pair() -> dict[str, AccountRead]:
    return {
        "acc-1": _account(account_id="acc-1", user_id=1, phone="70000000001"),
        "acc-2": _account(account_id="acc-2", user_id=2, phone="70000000002"),
    }


@pytest.mark.asyncio
async def test_dm_actions_carry_the_recipients_phone(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every DM action must ship the RECIPIENT's phone, on both dialogue paths.

    A cold session cannot resolve a bare ``user_id``; without the phone the
    gateway gives up and no inter-account DM leaves the fleet. The sender's own
    phone would resolve nothing either, so the pairing is asserted explicitly.
    """
    from services.warming._chat import _open_with_partner, _reply_to_partner  # noqa: PLC0415

    accounts = {
        "acc-a": _account(account_id="acc-a", user_id=1, phone="70000000001"),
        "acc-b": _account(account_id="acc-b", user_id=2, phone="70000000002"),
    }
    recorder = _Recorder()
    texts = iter(["so what have you been up to", "quiet week here, mostly reading"])

    async def gen(_request: object) -> GeminiResult:
        return GeminiResult(status="ok", text=next(texts, "a spare line for a retry"))

    monkeypatch.setattr(_seams, "execute", recorder.execute)
    monkeypatch.setattr(_seams, "generate_text", gen)
    secret = await load_warming_settings()

    # acc-a opens (smaller id wins the tiebreak); its DM lands in acc-b's inbox,
    # which acc-b then reads and answers — exercising all three action sites.
    assert (await _open_with_partner("acc-a", ["acc-b"], secret, accounts)).messages_sent == 1
    incoming = await oldest_unreplied_for("acc-b")
    assert incoming is not None
    assert (await _reply_to_partner("acc-b", incoming, secret, accounts)).messages_sent == 1

    sends = [
        (sender, a.user_id, a.peer_phone)
        for sender, a in recorder.actions
        if a.action_type == "send_dm"
    ]
    reads = [
        (sender, a.user_id, a.peer_phone)
        for sender, a in recorder.actions
        if a.action_type == "mark_dm_read"
    ]
    # Each sender carries the OTHER account's phone, never its own.
    assert sends == [("acc-a", 2, "70000000002"), ("acc-b", 1, "70000000001")]
    assert reads == [("acc-b", 1, "70000000001")]


def test_peer_unresolved_constant_tracks_the_gateway_error_class() -> None:
    """The skip branch matches on a string; a rename must not silently kill it.

    ``services/`` can't import the gateway's private exception, so the constant
    is pinned here instead.
    """
    from core.telegram_client._dm import DmPeerUnresolvedError  # noqa: PLC0415
    from services.warming._chat import _PEER_UNRESOLVED  # noqa: PLC0415

    assert DmPeerUnresolvedError.__name__ == _PEER_UNRESOLVED


@pytest.mark.asyncio
async def test_unresolvable_peer_skips_without_failing_the_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unaddressable partner must not park its sender.

    A ``failed`` cycle with no work done sends the account to the terminal
    ``error`` state, so counting someone else's privacy setting — or a missing
    phone — as a failure would kill an otherwise healthy sender.
    """
    from services.warming._chat import _open_with_partner  # noqa: PLC0415

    async def gen(_request: object) -> GeminiResult:
        return GeminiResult(status="ok", text="howdy")

    monkeypatch.setattr(_seams, "execute", _unresolvable)
    monkeypatch.setattr(_seams, "generate_text", gen)

    result = await _open_with_partner("acc-1", ["acc-2"], await load_warming_settings(), _pair())

    assert result.messages_sent == 0
    assert result.failures == 0
    assert result.last_failed_action is None
    # Billed, so the phone lookup it spent counts against the daily cap.
    assert result.attempted_actions == 1


@pytest.mark.asyncio
async def test_unresolvable_partner_stops_the_reply_from_re_arming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pending message must be consumed, and the turn abandoned at the read-ack.

    Left pending it resurfaces every cycle forever — ``_conversation_faded``
    needs 12 turns and a stuck pair never leaves one — each time paying a second
    lookup and a Gemini generation for a reply that cannot be delivered.
    """
    from services.warming._chat import _reply_to_partner  # noqa: PLC0415

    dispatched: list[str] = []
    generated = 0

    async def record_then_fail(account_id: str, action: TelegramAction) -> ActionResult:
        dispatched.append(action.action_type)
        return await _unresolvable(account_id, action)

    async def gen(_request: object) -> GeminiResult:
        nonlocal generated
        generated += 1
        return GeminiResult(status="ok", text="howdy")

    monkeypatch.setattr(_seams, "execute", record_then_fail)
    monkeypatch.setattr(_seams, "generate_text", gen)
    await record_dialogue_message("acc-2", "acc-1", "are you there")
    incoming = await oldest_unreplied_for("acc-1")
    assert incoming is not None

    result = await _reply_to_partner("acc-1", incoming, await load_warming_settings(), _pair())

    assert result.failures == 0
    assert result.attempted_actions == 1
    # Consumed, so the pair stops re-arming this same message next cycle.
    assert await oldest_unreplied_for("acc-1") is None
    # Bailed at the read-ack: no second lookup, no wasted generation.
    assert dispatched == ["mark_dm_read"]
    assert generated == 0
