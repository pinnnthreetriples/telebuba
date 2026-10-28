"""Smart rotation: a stopped account rests and comes back, its chats never sit idle."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.repositories import chat_broadcast as repository
from services.chat_broadcast import _rotation
from services.chat_broadcast._context import load_context
from tests.services.chat_broadcast.fakes import refused, run_to_end, seed

if TYPE_CHECKING:
    from tests.services.chat_broadcast.fakes import Clock, FakeTelegram


@pytest.mark.asyncio
async def test_a_short_flood_keeps_the_account_and_its_chats(
    telegram: FakeTelegram, clock: Clock
) -> None:
    campaign_id = await seed(accounts=("a1", "a2"), targets=("@alpha", "@beta"), messages=("Hi",))
    telegram.send_answers["a1"] = [refused("flood_wait", flood_wait_seconds=20)]
    started = clock.now()

    await run_to_end(campaign_id)

    targets = {t.chat_key: t for t in await repository.list_targets(campaign_id)}
    # Waiting 20 seconds is cheaper than another account joining the chat.
    assert (targets["alpha"].assigned_account_id, targets["alpha"].state) == ("a1", "done")
    assert targets["alpha"].handed_from_account_id is None
    assert clock.now() - started >= 20
    assert sorted(s.account_id for s in telegram.sent) == ["a1", "a2"]


@pytest.mark.asyncio
async def test_with_every_account_resting_the_run_waits_instead_of_stalling(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []

    async def _record(_level: str, event: str, **_fields: object) -> None:
        events.append(event)

    monkeypatch.setattr(_rotation, "log_event", _record)
    campaign_id = await seed(accounts=("a1",), targets=("@alpha",), messages=("one", "two"))
    telegram.send_answers["a1"] = [refused("flood_wait", flood_wait_seconds=3000)]

    await run_to_end(campaign_id)

    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert record.status == "done"
    assert telegram.texts() == ["one", "two"]
    assert events == ["chat_broadcast_account_resting", "chat_broadcast_account_back"]


@pytest.mark.asyncio
async def test_an_account_at_its_daily_limit_hands_even_joined_chats_to_one_with_room(
    telegram: FakeTelegram,
) -> None:
    """The case seen live: the limit is spent for hours, the chat it sits in must not wait."""
    campaign_id = await seed(
        accounts=("a1", "a2"),
        targets=("@chat1", "@chat2"),
        messages=("one", "two"),
        per_hour=50,
        per_day=1,
    )
    # a2's own chat refuses it, so a2 has spent nothing of its day.
    telegram.send_answers["a2"] = [refused(error_type="ChatWriteForbiddenError")]

    await run_to_end(campaign_id)

    chat1 = await repository.fetch_target(campaign_id, "chat1")
    assert chat1 is not None
    assert (chat1.state, chat1.assigned_account_id, chat1.handed_from_account_id) == (
        "done",
        "a2",
        "a1",
    )
    assert [(s.account_id, s.text) for s in telegram.sent] == [("a1", "one"), ("a2", "two")]
    roster = {r.account_id: r for r in await repository.list_roster(campaign_id)}
    assert roster["a1"].rest_reason == "day_limit"
    journal = await repository.list_journal(campaign_id)
    first = next(row for row in journal if row.account_id == "a1")
    # Back exactly when its one send of the day leaves the 24 h window.
    assert roster["a1"].resting_until_unix == first.created_unix + 86_400


@pytest.mark.asyncio
async def test_a_limit_ending_soon_keeps_the_chats_it_is_already_in(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.chat_broadcast, "handover_rest_seconds", 7200.0)
    campaign_id = await seed(
        accounts=("a1", "a2"),
        targets=("@chat1", "@chat2"),
        messages=("one", "two"),
        per_hour=1,
        per_day=50,
    )
    telegram.send_answers["a2"] = [refused(error_type="ChatWriteForbiddenError")]

    await run_to_end(campaign_id)

    chat1 = await repository.fetch_target(campaign_id, "chat1")
    assert chat1 is not None
    assert (chat1.state, chat1.assigned_account_id) == ("done", "a1")
    assert [(s.account_id, s.text) for s in telegram.sent] == [("a1", "one"), ("a1", "two")]
    assert ("a2", "chat1") not in telegram.joins


@pytest.mark.asyncio
@pytest.mark.usefixtures("telegram")
async def test_the_limit_ends_when_the_oldest_send_leaves_the_window() -> None:
    campaign_id = await seed(accounts=("a1",), per_hour=2, per_day=50)
    ctx = await load_context(campaign_id, "run-1")
    assert ctx is not None
    assert await _rotation.limit_ends(ctx, "a1") is None
    sent: list[int] = []
    for step in range(3):
        await _journal(campaign_id, step)
        sent.append(max(r.created_unix for r in await repository.list_journal(campaign_id)))

    ends = await _rotation.limit_ends(ctx, "a1")

    # Three sends against two an hour: the second one has to leave first.
    assert ends == (sorted(sent)[1] + 3600, "hour_limit")


@pytest.mark.asyncio
async def test_continue_brings_back_an_account_out_for_errors_but_not_a_dead_one(
    telegram: FakeTelegram,
) -> None:
    campaign_id = await seed(accounts=("a1", "a2"), targets=("@alpha", "@beta"), messages=("Hi",))
    await repository.halt_account(campaign_id, "a1", "errors_in_a_row")
    await repository.halt_account(campaign_id, "a2", "account_frozen")

    await run_to_end(campaign_id)

    roster = {r.account_id: r for r in await repository.list_roster(campaign_id)}
    assert (roster["a1"].state, roster["a1"].halted_reason) == ("active", None)
    assert (roster["a2"].state, roster["a2"].halted_reason) == ("halted", "account_frozen")
    assert {s.account_id for s in telegram.sent} == {"a1"}


async def _journal(campaign_id: str, step: int) -> None:
    from schemas.chat_broadcast_records import JournalKey  # noqa: PLC0415

    key = JournalKey(run_id="run-1", chat_key="alpha", round=1, step_index=step)
    assert await repository.claim_message(
        key, campaign_id=campaign_id, account_id="a1", kind="text"
    )
    await repository.settle_message(key, "sent", text="Hi")
