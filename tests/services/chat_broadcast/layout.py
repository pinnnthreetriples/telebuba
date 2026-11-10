"""A campaign laid out chat by chat, with the run holding its accounts: for scheduler tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NamedTuple

from core.repositories import chat_broadcast as repository
from schemas.chat_broadcast_records import TargetSeed
from schemas.telegram_actions import BroadcastSendMessage
from services import _account_owner
from services.chat_broadcast import _seams
from services.chat_broadcast._context import load_context
from tests.services.chat_broadcast.fakes import seed

if TYPE_CHECKING:
    import pytest

    from schemas.telegram_actions import ActionResult, TelegramAction
    from services.chat_broadcast._context import RunContext
    from tests.services.chat_broadcast.fakes import Clock

QUEUED = "queued"


class Chat(NamedTuple):
    """One chat to lay out: who owns it, who is inside, and the state it is in."""

    key: str
    account: str
    member: str | None
    state: str
    extra: dict[str, Any]


def chat(
    key: str, account: str, member: str | None = None, state: str = QUEUED, **extra: Any
) -> Chat:
    return Chat(key, account, member, state, extra)


async def setup(
    accounts: tuple[str, ...], chats: list[Chat], **overrides: Any
) -> tuple[str, RunContext]:
    """A running campaign whose accounts the run holds, with ``chats`` laid out as given."""
    values = {"loop": True, "rounds": 0} | overrides
    campaign_id = await seed(accounts=accounts, targets=(), messages=("Hi",), **values)
    for account_id in accounts:
        assert _account_owner.try_claim(account_id, "chat_broadcast", campaign_id) is None
    await repository.replace_targets(
        campaign_id,
        [
            TargetSeed(
                chat_key=chat.key,
                raw=f"@{chat.key}",
                kind="public",
                username=chat.key,
                peer_id=100 + index,
                assigned_account_id=chat.account,
                member_account_id=chat.member,
            )
            for index, chat in enumerate(chats)
        ],
    )
    for chat in chats:
        await repository.update_target(campaign_id, chat.key, state=chat.state, **chat.extra)
    await repository.set_status(campaign_id, "running", run_id="run-1", round=1)
    ctx = await load_context(campaign_id, "run-1")
    assert ctx is not None
    return campaign_id, ctx


class SendLog:
    """When (on the engine's clock) and by whom each message went out, by chat."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, clock: Clock) -> None:
        self.sends: list[tuple[int, str, str]] = []
        original = _seams.execute

        async def execute(account_id: str, action: TelegramAction) -> ActionResult:
            result = await original(account_id, action)
            if result.status == "ok" and isinstance(action, BroadcastSendMessage):
                self.sends.append((clock.now(), account_id, action.chat))
            return result

        monkeypatch.setattr(_seams, "execute", execute)

    def by_chat(self, peer_ids: dict[str, str]) -> dict[str, list[int]]:
        wanted = {peer: key for key, peer in peer_ids.items()}
        grouped: dict[str, list[int]] = {key: [] for key in peer_ids}
        for at, _account, chat in self.sends:
            grouped[wanted[chat]].append(at)
        return grouped
