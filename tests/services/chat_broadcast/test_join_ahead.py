"""An account resting on its own limit joins its next chats.

The pause after a join then runs out during the rest instead of after it. A rest Telegram
imposed (a flood, a spam block) joins nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.repositories import chat_broadcast as repository
from schemas.chat_broadcast_records import JournalKey
from schemas.telegram_actions import JoinChannel
from services.chat_broadcast import _seams
from tests.services.chat_broadcast.fakes import refused, run_to_end, seed
from tests.services.chat_broadcast.layout import SendLog

if TYPE_CHECKING:
    from schemas.telegram_actions import ActionResult, TelegramAction
    from tests.services.chat_broadcast.fakes import Clock, FakeTelegram

_HOUR = 3600


def _join_times(monkeypatch: pytest.MonkeyPatch, clock: Clock) -> dict[str, int]:
    """When each chat was first joined, on the engine's clock."""
    joined: dict[str, int] = {}
    original = _seams.execute

    async def execute(account_id: str, action: TelegramAction) -> ActionResult:
        if isinstance(action, JoinChannel):
            joined.setdefault(action.channel or "", clock.now())
        return await original(account_id, action)

    monkeypatch.setattr(_seams, "execute", execute)
    return joined


@pytest.mark.asyncio
@pytest.mark.usefixtures("telegram")
async def test_a_limit_rest_is_spent_joining_the_chats_it_is_not_in_yet(
    clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seen live: an account at its limit held 20 chats it was not in, and sat idle."""
    log = SendLog(monkeypatch, clock)
    campaign_id = await seed(
        accounts=("a1",),
        targets=("@alpha",),
        messages=("Hi",),
        per_hour=1,
        per_day=50,
        join_delay_minutes=60,
    )
    # The hour is already spent when the run starts.
    key = JournalKey(run_id="old", chat_key="elsewhere", round=1, step_index=0)
    assert await repository.claim_message(
        key, campaign_id=campaign_id, account_id="a1", kind="text"
    )
    await repository.settle_message(key, "sent", text="Hi")
    started = clock.now()

    await run_to_end(campaign_id)

    [(sent_at, _account, _chat)] = log.sends
    # Joined during the hour's rest, so the hour's pause after the join was over by its
    # end: written once the limit let it, not an hour after that.
    assert sent_at - started < _HOUR + 300


@pytest.mark.asyncio
async def test_a_flood_rest_joins_nothing(
    telegram: FakeTelegram, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    joined = _join_times(monkeypatch, clock)
    campaign_id = await seed(accounts=("a1",), targets=("@alpha", "@beta"), messages=("Hi",))
    telegram.send_answers["a1"] = [refused("flood_wait", flood_wait_seconds=3000)]

    await run_to_end(campaign_id)

    assert joined["beta"] - joined["alpha"] >= 3000
