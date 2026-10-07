"""The board's gear and repeated messages: pauses a run reads live, copies in a round."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.repositories import chat_broadcast as repository
from core.repositories.logs import list_recent_logs
from schemas.chat_broadcast import (
    ChatBroadcastPace,
    ChatBroadcastSettings,
    ChatBroadcastSettingsUpdate,
)
from services import chat_broadcast as service
from services.chat_broadcast import _seams, _state
from services.chat_broadcast._context import RunContext, load_context, usable_chain
from services.chat_broadcast._errors import ChatBroadcastConflictError
from tests.services.chat_broadcast.fakes import run_to_end, seed

if TYPE_CHECKING:
    from tests.services.chat_broadcast.fakes import Clock, FakeTelegram


def _pace(*, messages: int = 3, chats: int = 30, rest: int = 60) -> ChatBroadcastPace:
    return ChatBroadcastPace.model_validate(
        {
            "between_messages": {"min": messages, "max": messages},
            "between_chats": {"min": chats, "max": chats},
            "rest_minutes": {"min": rest, "max": rest},
        }
    )


async def _with_repeats(campaign_id: str, repeats: list[int]) -> None:
    current = await service.load_settings(campaign_id)
    assert current is not None
    messages = [
        message.model_copy(update={"repeat": repeat})
        for message, repeat in zip(current.settings.messages, repeats, strict=True)
    ]
    await service.save_settings(
        campaign_id,
        ChatBroadcastSettingsUpdate(
            expected_updated_at=current.updated_at,
            name=current.name,
            account_ids=current.account_ids,
            settings=current.settings.model_copy(update={"messages": messages}),
        ),
    )


@pytest.mark.asyncio
async def test_a_repeated_message_goes_out_its_copies_before_the_next(
    telegram: FakeTelegram,
) -> None:
    campaign_id = await seed(accounts=("a1",), targets=("@alpha",), messages=("First", "Second"))
    await _with_repeats(campaign_id, [3, 1])

    await run_to_end(campaign_id)

    assert telegram.texts() == ["First", "First", "First", "Second"]
    journal = sorted(await repository.list_journal(campaign_id), key=lambda row: row.id)
    assert [row.step_index for row in journal] == [0, 1, 2, 3]
    board = await service.load_board(campaign_id)
    assert board is not None
    assert [row.planned_total for row in board.rows] == [4]


def test_every_copy_of_the_first_message_is_the_ai_s() -> None:
    value = ChatBroadcastSettings.model_validate(
        {
            "first_message": "ai",
            "ai_brief": "Offer the exchange",
            "messages": [{"text": "", "repeat": 2}, {"text": "Then this", "repeat": 2}],
        }
    )
    chain = usable_chain(value)

    ctx = RunContext(campaign_id="c", run_id="r", settings=value, chain=chain)
    assert [ctx.ai_first(step) for step in range(len(chain))] == [True, True, False, False]


@pytest.mark.asyncio
async def test_the_pauses_save_while_the_rest_stays_locked() -> None:
    campaign_id = await seed()
    await repository.set_status(campaign_id, "running", run_id="run-1")
    current = await service.load_settings(campaign_id)
    assert current is not None

    saved = await service.save_pace(campaign_id, _pace(messages=20, chats=200, rest=5))

    assert saved is not None
    assert saved.updated_at != current.updated_at
    assert saved.settings.between_messages.model_dump() == {"min": 20, "max": 20}
    assert saved.settings.between_chats.model_dump() == {"min": 200, "max": 200}
    assert saved.settings.rest_minutes.model_dump() == {"min": 5, "max": 5}
    # Everything else is untouched.
    assert saved.settings.messages == current.settings.messages
    with pytest.raises(ChatBroadcastConflictError, match="campaign_running"):
        await service.save_settings(
            campaign_id,
            ChatBroadcastSettingsUpdate(
                expected_updated_at=saved.updated_at,
                name=saved.name,
                account_ids=saved.account_ids,
                settings=saved.settings,
            ),
        )
    assert await service.save_pace("missing", _pace()) is None


@pytest.mark.asyncio
async def test_a_run_sees_the_new_pauses_and_a_new_run_reads_the_row() -> None:
    campaign_id = await seed()
    ctx = await load_context(campaign_id, "run-1")
    assert ctx is not None
    assert ctx.pace.between_messages.model_dump() == {"min": 3, "max": 8}

    await service.save_pace(campaign_id, _pace(messages=40))

    assert ctx.pace.between_messages.model_dump() == {"min": 40, "max": 40}
    fresh = await load_context(campaign_id, "run-2")
    assert fresh is not None
    assert _state.live_pace(campaign_id) is None
    assert fresh.pace.between_messages.model_dump() == {"min": 40, "max": 40}


@pytest.mark.asyncio
async def test_a_rest_under_way_is_drawn_again_from_its_start(clock: Clock) -> None:
    campaign_id = await seed()
    started = clock.now() - 600
    _state.note_rest_started(campaign_id, started)
    await repository.set_runtime(campaign_id, round=1, rest_until_unix=started + 3600)

    await service.save_pace(campaign_id, _pace(rest=20))

    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert record.rest_until_unix == started + 20 * 60
    logs = [
        row
        for row in await list_recent_logs(limit=50)
        if row.event == "chat_broadcast_pace_changed"
    ]
    assert [row.extra.get("until") for row in logs] == [started + 20 * 60]


@pytest.mark.asyncio
async def test_a_rest_is_left_alone_when_unchanged_or_its_start_is_unknown(clock: Clock) -> None:
    campaign_id = await seed()
    until = clock.now() + 3600
    await repository.set_runtime(campaign_id, round=1, rest_until_unix=until)

    # Begun before a restart: this process never saw it start.
    await service.save_pace(campaign_id, _pace(rest=20))
    _state.note_rest_started(campaign_id, clock.now())
    # The rest range itself did not change.
    await service.save_pace(campaign_id, _pace(rest=20, messages=9))

    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert record.rest_until_unix == until


@pytest.mark.asyncio
async def test_a_move_never_puts_a_rest_into_the_next_round() -> None:
    campaign_id = await seed()
    await repository.set_runtime(campaign_id, round=2, rest_until_unix=None)

    assert not await repository.move_rest(campaign_id, round_number=2, until=1)
    await repository.set_runtime(campaign_id, rest_until_unix=10)
    assert not await repository.move_rest(campaign_id, round_number=1, until=1)
    assert await repository.move_rest(campaign_id, round_number=2, until=1)


@pytest.mark.asyncio
async def test_shortening_the_rest_mid_rest_opens_the_next_round_early(
    telegram: FakeTelegram, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign_id = await seed(
        accounts=("a1",),
        targets=("@alpha",),
        messages=("Hi",),
        loop=True,
        rounds=2,
        rest_minutes={"min": 120, "max": 120},
    )
    started = clock.now()
    tick = _seams.sleep
    shortened = False

    async def _sleep(seconds: float) -> None:
        nonlocal shortened
        await tick(seconds)
        record = await repository.fetch_campaign(campaign_id)
        if not shortened and record is not None and record.rest_until_unix is not None:
            shortened = True
            await service.save_pace(campaign_id, _pace(rest=1))

    monkeypatch.setattr(_seams, "sleep", _sleep)

    await run_to_end(campaign_id)

    assert shortened
    assert len(telegram.sent) == 2
    # Far less than the two hours the rest was drawn at.
    assert clock.now() - started < 15 * 60
    # The run settled: nothing of its pauses stays in memory.
    assert _state.live_pace(campaign_id) is None
    assert _state.rest_started(campaign_id) is None


@pytest.mark.asyncio
async def test_a_deleted_campaign_leaves_no_pauses_in_memory() -> None:
    campaign_id = await seed()
    await service.save_pace(campaign_id, _pace(messages=9))
    _state.note_rest_started(campaign_id, 1)

    assert await service.delete_campaign(campaign_id)

    assert _state.live_pace(campaign_id) is None
    assert _state.rest_started(campaign_id) is None
