"""A campaign a barrier-round run left behind goes on under chats with their own rounds.

The run restarted mid-campaign finds the old state: one shared rest on the campaign row, or
chats waiting for their round to close with no end of their own. Nothing is lost, nothing
is written twice, and no chat is sent into its next round before the rest it was owed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.repositories import chat_broadcast as repository
from schemas.chat_broadcast_records import TargetSeed
from services import chat_broadcast as service
from services.chat_broadcast import _runtime
from tests.services.chat_broadcast.fakes import seed
from tests.services.chat_broadcast.layout import SendLog

if TYPE_CHECKING:
    from tests.services.chat_broadcast.fakes import Clock, FakeTelegram

_MINUTE = 60
_CHATS = ("alpha", "beta", "gamma", "delta")


def _member(key: str) -> TargetSeed:
    return TargetSeed(
        chat_key=key,
        raw=f"@{key}",
        kind="public",
        username=key,
        peer_id=abs(hash(key)) % 10**9 + 1,
        assigned_account_id="a1",
        member_account_id="a1",
    )


async def _left_by_a_barrier_run(*, rest_until: int | None) -> str:
    """Round 2 of 3 was over: two chats through, one skipped for an error, one kept."""
    campaign_id = await seed(
        accounts=("a1",),
        targets=tuple(f"@{key}" for key in _CHATS),
        messages=("Hi",),
        loop=True,
        rounds=3,
        rest_minutes={"min": 1, "max": 1},
    )
    await repository.replace_targets(campaign_id, [_member(key) for key in _CHATS])
    await repository.set_status(campaign_id, "running", run_id="run-1", round=2, started_unix=1)
    await repository.set_runtime(campaign_id, rest_until_unix=rest_until)
    for key in ("alpha", "beta"):
        await repository.update_target(campaign_id, key, state="round_done", round=2, step_index=1)
    await repository.update_target(
        campaign_id, "gamma", state="skipped", skip_reason="error", round=2
    )
    await repository.update_target(
        campaign_id, "delta", state="skipped", skip_reason="deleted", ignore_deleted=1, round=2
    )
    return campaign_id


async def _resume_to_end(campaign_id: str) -> None:
    await service.reconcile_chat_broadcast_on_startup()
    await _runtime._TASKS[campaign_id]


@pytest.mark.asyncio
async def test_the_shared_rest_becomes_every_chats_own_and_is_neither_cut_nor_stretched(
    telegram: FakeTelegram, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = SendLog(monkeypatch, clock)
    until = clock.now() + 30 * _MINUTE
    campaign_id = await _left_by_a_barrier_run(rest_until=until)

    await _resume_to_end(campaign_id)

    # All four were owed that rest: none went before it ended, and all went after.
    assert len(log.sends) == len(_CHATS)
    assert min(at for at, _account, _chat in log.sends) >= until
    assert max(at for at, _account, _chat in log.sends) < until + 10 * _MINUTE
    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert (record.status, record.rest_until_unix) == ("done", None)
    states = {t.chat_key: (t.state, t.round) for t in await repository.list_targets(campaign_id)}
    assert states == dict.fromkeys(_CHATS, ("done", 3))
    # Written once each, in round 3, and nothing twice.
    journal = await repository.list_journal(campaign_id, "run-1")
    assert sorted((row.chat_key, row.round) for row in journal) == [
        (key, 3) for key in sorted(_CHATS)
    ]
    assert telegram.joins == []


@pytest.mark.asyncio
async def test_chats_a_barrier_left_waiting_with_no_rest_rest_from_now_not_at_once(
    telegram: FakeTelegram, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = SendLog(monkeypatch, clock)
    campaign_id = await _left_by_a_barrier_run(rest_until=None)
    started = clock.now()

    await _resume_to_end(campaign_id)

    # No instant next round: the rest of the settings runs from this moment.
    assert len(log.sends) == len(_CHATS)
    assert min(at for at, _account, _chat in log.sends) >= started + _MINUTE
    states = {t.chat_key: (t.state, t.round) for t in await repository.list_targets(campaign_id)}
    assert states == dict.fromkeys(_CHATS, ("done", 3))
    assert telegram.joins == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("telegram")
async def test_a_stale_barrier_rest_is_handed_to_the_chats_once_and_the_row_forgets_it(
    clock: Clock,
) -> None:
    until = clock.now() + 600
    campaign_id = await _left_by_a_barrier_run(rest_until=until)

    assert await repository.adopt_barrier_rest(campaign_id) == len(_CHATS)
    assert await repository.adopt_barrier_rest(campaign_id) == 0

    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert (record.round, record.rest_until_unix) == (2, None)
    ends = {t.chat_key: t.next_action_unix for t in await repository.list_targets(campaign_id)}
    assert ends == dict.fromkeys(_CHATS, until)
