"""Live DMs (item 4.7): a reaction or a sticker instead of a reply, a forwarded post opener.

Every variant stays behind the DM gates; media needs a few exchanges in the pair first.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.config import settings
from core.db import oldest_unreplied_for, recent_pair_messages, record_dialogue_message
from core.repositories.dialogues import pair_key
from core.repositories.warming_dm_pause import store_dm_paused_until
from schemas.gemini import GeminiResult
from schemas.warming import WarmingCycleRequest
from services import warming
from services.warming import _chat_live, _seams
from tests.services.warming._support import (
    _Recorder,
    _seed_channel,
    _seed_two_warming_accounts,
    _set_settings,
)


async def _setup(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    await _set_settings(chat=True, reactions=False, key="gemini-key")
    await _seed_two_warming_accounts()
    return recorder


def _request(account_id: str) -> WarmingCycleRequest:
    return WarmingCycleRequest(account_id=account_id, dm_allowed=True)


@pytest.mark.asyncio
async def test_a_reaction_answers_instead_of_text_and_ends_the_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.warming, "dm_reaction_probability", 1.0)
    recorder = await _setup(monkeypatch)
    await record_dialogue_message("acc-2", "acc-1", "hola!", replied=False)

    result = await warming.run_one_cycle(_request("acc-1"))

    types = recorder.types()
    assert "warm_dm_react" in types
    assert "send_dm" not in types
    assert result.messages_sent == 0
    assert await oldest_unreplied_for("acc-1") is None


@pytest.mark.asyncio
async def test_a_sticker_needs_a_few_exchanges_first(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "dm_sticker_probability", 1.0)
    monkeypatch.setattr(settings.warming, "dm_media_min_exchanges", 3)

    async def gen(_request: object) -> GeminiResult:
        return GeminiResult(status="ok", text="todo bien")

    monkeypatch.setattr(_seams, "generate_text", gen)
    recorder = await _setup(monkeypatch)
    await record_dialogue_message("acc-2", "acc-1", "hola!", replied=False)

    await warming.run_one_cycle(_request("acc-1"))
    assert "warm_dm_sticker" not in recorder.types()
    assert "send_dm" in recorder.types()

    await record_dialogue_message("acc-2", "acc-1", "jaja", replied=False)
    await warming.run_one_cycle(_request("acc-1"))
    assert "warm_dm_sticker" in recorder.types()
    history = await recent_pair_messages(pair_key("acc-1", "acc-2"), 10)
    assert history[-1].text in settings.warming.dm_sticker_moods


@pytest.mark.asyncio
async def test_an_opener_may_forward_a_post_just_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "dm_forward_probability", 1.0)
    monkeypatch.setattr(settings.warming, "dm_media_min_exchanges", 0)
    recorder = await _setup(monkeypatch)

    result = await warming.run_one_cycle(_request("acc-1"))

    forward = next(a for _id, a in recorder.actions if a.action_type == "warm_dm_forward")
    assert getattr(forward, "message_id", None) in {101, 102}
    word = recorder.types().index("warm_dm_forward") + 1
    assert recorder.actions[word][1].action_type == "send_dm"
    assert getattr(recorder.actions[word][1], "text", None) == "смотри"
    assert result.messages_sent == 1
    history = await recent_pair_messages(pair_key("acc-1", "acc-2"), 10)
    assert history[-1].text == "смотри"


@pytest.mark.asyncio
async def test_a_forward_whose_word_fails_is_still_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.warming, "dm_forward_probability", 1.0)
    monkeypatch.setattr(settings.warming, "dm_media_min_exchanges", 0)
    recorder = await _setup(monkeypatch)
    recorder.flood_on.add("send_dm")

    result = await warming.run_one_cycle(_request("acc-1"))

    assert result.status == "flood_wait"
    history = await recent_pair_messages(pair_key("acc-1", "acc-2"), 10)
    assert [m.text for m in history] == ["📎"]


@pytest.mark.asyncio
async def test_a_reaction_only_answers_the_newest_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.warming, "dm_reaction_probability", 1.0)

    async def gen(_request: object) -> GeminiResult:
        return GeminiResult(status="ok", text="todo bien")

    monkeypatch.setattr(_seams, "generate_text", gen)
    recorder = await _setup(monkeypatch)
    await record_dialogue_message("acc-2", "acc-1", "hola!", replied=False)
    await record_dialogue_message("acc-2", "acc-1", "estás?", replied=False)

    await warming.run_one_cycle(_request("acc-1"))

    # The oldest message is not the pair's newest: it gets a text answer, no reaction.
    assert "warm_dm_react" not in recorder.types()
    assert "send_dm" in recorder.types()


@pytest.mark.asyncio
async def test_the_fleet_pause_stops_a_live_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "dm_reaction_probability", 1.0)
    recorder = await _setup(monkeypatch)
    await record_dialogue_message("acc-2", "acc-1", "hola!", replied=False)

    async def paused() -> bool:
        return True

    monkeypatch.setattr(_chat_live, "fleet_dm_paused_now", paused)
    await warming.run_one_cycle(_request("acc-1"))
    assert "warm_dm_react" not in recorder.types()
    assert await oldest_unreplied_for("acc-1") is not None


@pytest.mark.asyncio
async def test_no_live_dm_while_dms_are_paused_fleet_wide(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "dm_reaction_probability", 1.0)
    monkeypatch.setattr(settings.warming, "dm_forward_probability", 1.0)
    monkeypatch.setattr(settings.warming, "dm_media_min_exchanges", 0)
    recorder = await _setup(monkeypatch)
    await store_dm_paused_until((datetime.now(UTC) + timedelta(hours=5)).isoformat())
    await record_dialogue_message("acc-2", "acc-1", "hola!", replied=False)

    await warming.run_one_cycle(_request("acc-1"))
    assert not [t for t in recorder.types() if t.startswith("warm_dm_")]
