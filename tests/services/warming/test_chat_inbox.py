"""Inbox ordering and opener back-pressure for the inter-account dialogue turn.

Two bugs live here: the inbox used to be LIFO (newer arrivals shadowed older
ones forever) and the opener used to ignore its own unanswered DMs (up to a
dozen one-sided messages to the same partner in a row).
"""

from __future__ import annotations

import pytest

from core.db import (
    create_account,
    load_warming_settings,
    oldest_unreplied_for,
    record_dialogue_message,
    replace_dialogue_pairs,
    update_account_from_session_check,
)
from core.repositories import dialogues as dialogues_repo
from schemas.accounts import AccountCreate, AccountRead
from schemas.gemini import GeminiResult
from schemas.telegram_actions import ActionResult, SendDirectMessage, TelegramAction
from schemas.telegram_session import TelegramSessionCheckResult
from services.warming import _seams
from services.warming._chat import _maybe_inter_account_chat, _open_with_partner
from tests.services.warming._support import _account


class _Sender:
    def __init__(self) -> None:
        self.actions: list[tuple[str, TelegramAction]] = []

    async def execute(self, account_id: str, action: TelegramAction) -> ActionResult:
        self.actions.append((account_id, action))
        return ActionResult(status="ok", action_type=action.action_type, account_id=account_id)

    def types(self) -> list[str]:
        return [action.action_type for _account_id, action in self.actions]


async def _gen(_request: object) -> GeminiResult:
    return GeminiResult(status="ok", text="как оно вообще?")


def _wire(monkeypatch: pytest.MonkeyPatch) -> _Sender:
    sender = _Sender()
    monkeypatch.setattr(_seams, "execute", sender.execute)
    monkeypatch.setattr(_seams, "generate_text", _gen)
    return sender


async def _seed_pair() -> None:
    await create_account(AccountCreate(account_id="acc-1"))
    await create_account(AccountCreate(account_id="acc-2"))
    await update_account_from_session_check(
        TelegramSessionCheckResult(
            account_id="acc-2",
            session_path="acc-2",
            status="alive",
            is_temporary=False,
            user_id=999,
        ),
    )
    await replace_dialogue_pairs([("acc-1", "acc-2")])


def _accounts(*ids_and_user_ids: tuple[str, int]) -> dict[str, AccountRead]:
    return {
        account_id: _account(account_id=account_id, user_id=user_id)
        for account_id, user_id in ids_and_user_ids
    }


@pytest.mark.asyncio
async def test_orphans_are_drained_in_one_pass_so_the_partner_still_gets_answered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run of ex-partner messages at the head must not cost one cycle each.

    The inbox is FIFO, so orphans (from accounts that are no longer partners
    after a reshuffle) sit in front of the real partner message. Draining one
    per cycle would burn N dialogue turns on bookkeeping before the partner got
    a reply; the orphan branch clears the whole run and replies the same turn.
    """
    sender = _wire(monkeypatch)
    await _seed_pair()
    for i in range(3):
        await record_dialogue_message(f"ghost-{i}", "acc-1", f"старое {i}")
    await record_dialogue_message("acc-2", "acc-1", "привет!")

    result = await _maybe_inter_account_chat("acc-1", await load_warming_settings())

    assert result.messages_sent == 1
    assert sender.types().count("send_dm") == 1
    # Every orphan consumed and the partner message answered — inbox empty.
    assert await oldest_unreplied_for("acc-1") is None


@pytest.mark.asyncio
async def test_a_partner_whose_sends_always_fail_does_not_pin_the_fifo_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A permanently undeliverable reply must not block every other partner.

    acc-2's message is the oldest, and every DM to acc-2 comes back a plain
    ``failed`` — blocked, privacy-restricted, deactivated: anything that is not
    ``DmPeerUnresolvedError``. Re-arming that row put it straight back at the
    head of a FIFO inbox, so acc-3 was never answered and every cycle forever
    burned a read-ack, a full Gemini generation and a doomed send.
    """
    dm_ok: list[int] = []
    generated = iter(f"реплика номер {n}, как сам" for n in range(50))

    async def execute(account_id: str, action: TelegramAction) -> ActionResult:
        if isinstance(action, SendDirectMessage):
            if action.user_id == 222:
                status = "failed"
            else:
                status = "ok"
                dm_ok.append(action.user_id)
        else:
            status = "ok"
        return ActionResult(status=status, action_type=action.action_type, account_id=account_id)

    async def gen(_request: object) -> GeminiResult:
        return GeminiResult(status="ok", text=next(generated))

    monkeypatch.setattr(_seams, "execute", execute)
    monkeypatch.setattr(_seams, "generate_text", gen)

    for account_id, user_id in (("acc-1", 111), ("acc-2", 222), ("acc-3", 333)):
        await create_account(AccountCreate(account_id=account_id))
        await update_account_from_session_check(
            TelegramSessionCheckResult(
                account_id=account_id,
                session_path=account_id,
                status="alive",
                is_temporary=False,
                user_id=user_id,
            ),
        )
    await replace_dialogue_pairs([("acc-1", "acc-2"), ("acc-1", "acc-3")])
    # acc-2's message is older, so FIFO serves it first every single cycle.
    await record_dialogue_message("acc-2", "acc-1", "старое от acc-2")
    await record_dialogue_message("acc-3", "acc-1", "новое от acc-3")

    secret = await load_warming_settings()
    for _ in range(6):
        await _maybe_inter_account_chat("acc-1", secret)

    assert 333 in dm_ok, "acc-3 never got answered: acc-2's failing row pinned the FIFO head"


@pytest.mark.asyncio
async def test_opener_waits_for_an_answer_before_dming_the_same_partner_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No second opener while our last DM to that partner is still unanswered."""
    sender = _wire(monkeypatch)
    await _seed_pair()
    # We already opened; acc-2 has not replied (replied=0 on our outgoing row).
    await record_dialogue_message("acc-1", "acc-2", "привет!")

    result = await _open_with_partner(
        "acc-1",
        ["acc-2"],
        await load_warming_settings(),
        _accounts(("acc-1", 1), ("acc-2", 999)),
    )

    assert result.messages_sent == 0
    assert sender.actions == []


@pytest.mark.asyncio
async def test_opener_ignores_a_pending_message_older_than_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dead thread must not block the pair forever.

    A permanent block would silence any pair whose partner went quiet, and as
    those rows accumulate fleet-wide the opener would stop firing at all. The
    block is bounded by ``dialogue_conversation_window_hours``.
    """
    sender = _wire(monkeypatch)
    await _seed_pair()
    with pytest.MonkeyPatch.context() as old_clock:
        old_clock.setattr(dialogues_repo, "_now_iso", lambda: "2020-01-01T00:00:00+00:00")
        await record_dialogue_message("acc-1", "acc-2", "привет!")

    result = await _open_with_partner(
        "acc-1",
        ["acc-2"],
        await load_warming_settings(),
        _accounts(("acc-1", 1), ("acc-2", 999)),
    )

    assert result.messages_sent == 1
    assert sender.types() == ["send_dm"]


@pytest.mark.asyncio
async def test_opener_blocks_only_the_unanswered_partner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One query covers the whole partner set — a partner we owe nothing stays open."""
    sender = _wire(monkeypatch)
    await _seed_pair()
    await create_account(AccountCreate(account_id="acc-3"))
    await record_dialogue_message("acc-1", "acc-2", "привет!")

    result = await _open_with_partner(
        "acc-1",
        ["acc-2", "acc-3"],
        await load_warming_settings(),
        _accounts(("acc-1", 1), ("acc-2", 999), ("acc-3", 777)),
    )

    assert result.messages_sent == 1
    assert sender.types() == ["send_dm"]
    dm = sender.actions[0][1]
    assert isinstance(dm, SendDirectMessage)
    assert dm.user_id == 777  # acc-3, not the partner who owes us an answer
