"""The chain: pauses, photo and post, verdicts, errors in a row, limits and volume."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.repositories import chat_broadcast as repository
from services.chat_broadcast import _seams
from tests.services.chat_broadcast.fakes import refused, run_to_end, seed

if TYPE_CHECKING:
    from schemas.telegram_actions import ActionResult
    from tests.services.chat_broadcast.fakes import Clock, FakeTelegram


@pytest.mark.asyncio
async def test_messages_go_in_order_with_the_pause_between_them(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch, clock: Clock
) -> None:
    pauses: list[float] = []
    original = _seams.sleep

    async def _record(seconds: float) -> None:
        pauses.append(seconds)
        await original(seconds)

    monkeypatch.setattr(_seams, "sleep", _record)
    campaign_id = await seed(
        accounts=("a1",),
        targets=("@alpha",),
        messages=("one", "two", "three"),
        between_messages={"min": 3, "max": 8},
    )

    await run_to_end(campaign_id)

    assert telegram.texts() == ["one", "two", "three"]
    assert sum(1 for pause in pauses if 3 <= pause <= 8) >= 2
    assert clock.now() > 0


@pytest.mark.asyncio
@pytest.mark.usefixtures("telegram")
async def test_the_board_knows_when_the_next_message_goes_out(
    monkeypatch: pytest.MonkeyPatch, clock: Clock
) -> None:
    seen: list[tuple[float, int | None]] = []
    original = _seams.sleep
    ids: list[str] = []

    async def _record(seconds: float) -> None:
        target = await repository.fetch_target(ids[0], "alpha")
        if target is not None and target.state == "writing":
            seen.append((seconds, target.next_action_unix))
            assert target.next_action_unix == clock.now() + int(seconds)
        await original(seconds)

    monkeypatch.setattr(_seams, "sleep", _record)
    campaign_id = await seed(
        accounts=("a1",),
        targets=("@alpha",),
        messages=("one", "two"),
        between_messages={"min": 3, "max": 8},
    )
    ids.append(campaign_id)

    await run_to_end(campaign_id)

    assert len(seen) == 1
    final = await repository.fetch_target(campaign_id, "alpha")
    assert final is not None
    assert final.next_action_unix is None


@pytest.mark.asyncio
async def test_typing_scales_with_the_text(telegram: FakeTelegram) -> None:
    campaign_id = await seed(
        accounts=("a1",), targets=("@alpha",), messages=("x" * 60,), typing=True
    )

    await run_to_end(campaign_id)

    assert 1.5 <= telegram.sent[0].typing <= 8.0


@pytest.mark.asyncio
async def test_a_photo_and_a_post(telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch) -> None:
    media_id = "a" * 64 + ".png"

    async def _photo(_media_id: str) -> bytes:
        return b"png"

    monkeypatch.setattr("services.chat_broadcast._context.read_photo", _photo)
    campaign_id = await seed(
        accounts=("a1",),
        targets=("@alpha",),
        messages=(),
    )
    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    from schemas.chat_broadcast import (  # noqa: PLC0415
        ChatBroadcastSettings,
        ChatBroadcastSettingsUpdate,
    )
    from services import chat_broadcast as service  # noqa: PLC0415

    current = await service.load_settings(campaign_id)
    assert current is not None
    settings = current.settings.model_copy(
        update={
            "messages": ChatBroadcastSettings.model_validate(
                {
                    "messages": [
                        {"text": "caption", "photo": {"media_id": media_id, "name": "a.png"}},
                        {"kind": "post", "post": "t.me/mychannel/42"},
                        {"kind": "post", "post": ""},
                    ]
                }
            ).messages
        }
    )
    await service.save_settings(
        campaign_id,
        ChatBroadcastSettingsUpdate(
            expected_updated_at=current.updated_at,
            name="Crypto",
            account_ids=["a1"],
            settings=settings,
        ),
    )

    await run_to_end(campaign_id)

    assert [(s.text, s.photo, s.post) for s in telegram.sent] == [
        ("caption", True, None),
        ("", False, ("mychannel", 42)),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        (refused(error_type="ChatWriteForbiddenError"), "admin_only"),
        (refused(error_type="ChannelPrivateError"), "banned"),
    ],
)
async def test_a_chat_refusal_skips_the_chat_not_the_account(
    telegram: FakeTelegram, answer: ActionResult, reason: str
) -> None:
    campaign_id = await seed(accounts=("a1",), targets=("@alpha", "@beta"), messages=("Hi",))
    telegram.send_answers["a1"] = [answer]

    await run_to_end(campaign_id)

    targets = {t.chat_key: t for t in await repository.list_targets(campaign_id)}
    assert targets["alpha"].skip_reason == reason
    assert targets["beta"].state == "done"
    roster = await repository.list_roster(campaign_id)
    assert roster[0].state == "active"


@pytest.mark.asyncio
async def test_a_spam_blocked_account_hands_its_chats_over_and_they_join_again(
    telegram: FakeTelegram,
) -> None:
    campaign_id = await seed(
        accounts=("a1", "a2"), targets=("@alpha", "@beta"), messages=("one", "two")
    )
    telegram.send_answers["a1"] = [refused(error_type="UserBannedInChannelError")]

    await run_to_end(campaign_id)

    assert ("a2", "alpha") in telegram.joins
    # The refused step was not delivered, so the chat's new account sends it.
    assert sorted(s.text for s in telegram.sent if s.account_id == "a2") == [
        "one",
        "one",
        "two",
        "two",
    ]
    roster = {r.account_id: r for r in await repository.list_roster(campaign_id)}
    assert roster["a1"].halted_reason == "UserBannedInChannelError"
    targets = {t.chat_key: t for t in await repository.list_targets(campaign_id)}
    assert targets["alpha"].state == "done"
    assert targets["alpha"].assigned_account_id == "a2"


@pytest.mark.asyncio
async def test_with_every_account_halted_the_run_stalls(telegram: FakeTelegram) -> None:
    campaign_id = await seed(accounts=("a1",), targets=("@alpha", "@beta"), messages=("Hi",))
    telegram.send_answers["a1"] = [refused("peer_flood")]

    await run_to_end(campaign_id)

    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert record.status == "stalled"
    states = {t.state for t in await repository.list_targets(campaign_id)}
    assert states == {"waiting_account"}


@pytest.mark.asyncio
async def test_errors_in_a_row_take_the_account_out(telegram: FakeTelegram) -> None:
    campaign_id = await seed(
        accounts=("a1", "a2"),
        targets=("@chat1", "@chat2", "@chat3", "@chat4"),
        messages=("Hi",),
        max_consecutive_errors=2,
    )
    telegram.send_answers["a1"] = [refused(error_type="RPCError"), refused(error_type="RPCError")]

    await run_to_end(campaign_id)

    roster = {r.account_id: r for r in await repository.list_roster(campaign_id)}
    assert (roster["a1"].state, roster["a1"].halted_reason) == ("halted", "errors_in_a_row")
    skipped = [t for t in await repository.list_targets(campaign_id) if t.skip_reason == "error"]
    assert len(skipped) == 2


@pytest.mark.asyncio
async def test_with_skip_errors_off_a_failure_stops_the_run(telegram: FakeTelegram) -> None:
    campaign_id = await seed(accounts=("a1",), targets=("@alpha",), skip_errors=False)
    telegram.send_answers["a1"] = [refused(error_type="RPCError")]

    await run_to_end(campaign_id)

    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert (record.status, record.last_error) == ("failed", "RPCError")


@pytest.mark.asyncio
async def test_slow_mode_and_a_dropped_connection_retry_the_same_step(
    telegram: FakeTelegram,
) -> None:
    campaign_id = await seed(accounts=("a1",), targets=("@alpha",), messages=("one", "two"))
    telegram.send_answers["a1"] = [
        refused("slow_mode_wait", flood_wait_seconds=30),
        refused("unavailable", error_type="ConnectionError"),
    ]

    await run_to_end(campaign_id)

    assert telegram.texts() == ["one", "two"]
    kinds = [e.kind for e in await repository.list_events(campaign_id)]
    assert "reconnecting" in kinds


@pytest.mark.asyncio
async def test_an_unconfirmed_send_is_never_repeated(telegram: FakeTelegram) -> None:
    campaign_id = await seed(accounts=("a1",), targets=("@alpha",), messages=("one", "two"))
    telegram.send_answers["a1"] = [refused("unavailable", error_type="UnconfirmedRequest")]

    await run_to_end(campaign_id)

    assert telegram.texts() == ["two"]
    statuses = [row.status for row in await repository.list_journal(campaign_id)]
    assert statuses == ["unconfirmed", "sent"]


@pytest.mark.asyncio
async def test_the_volume_stops_the_run(telegram: FakeTelegram) -> None:
    campaign_id = await seed(
        accounts=("a1",), targets=("@chat1", "@chat2", "@chat3"), messages=("Hi",), stop_messages=2
    )

    await run_to_end(campaign_id)

    assert len(telegram.sent) == 2
    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert record.status == "done"


@pytest.mark.asyncio
async def test_an_account_at_its_hourly_limit_hands_unjoined_chats_to_others(
    telegram: FakeTelegram,
) -> None:
    campaign_id = await seed(
        accounts=("a1", "a2"),
        targets=("@chat1", "@chat2", "@chat3"),
        messages=("Hi",),
        per_hour=1,
        per_day=50,
    )
    # a2's own chat refuses it, so a2 has spent nothing of its hour.
    telegram.send_answers["a2"] = [refused(error_type="ChatWriteForbiddenError")]

    await run_to_end(campaign_id)

    targets = {t.chat_key: t for t in await repository.list_targets(campaign_id)}
    # a1 wrote chat1 and was out of room for chat3 before joining it; a2 had room.
    assert (targets["chat3"].assigned_account_id, targets["chat3"].handed_from_account_id) == (
        "a2",
        "a1",
    )
    assert ("a1", "chat3") not in telegram.joins


@pytest.mark.asyncio
async def test_with_skip_errors_off_the_failed_step_is_sent_on_continue(
    telegram: FakeTelegram,
) -> None:
    campaign_id = await seed(accounts=("a1",), targets=("@alpha",), skip_errors=False)
    telegram.send_answers["a1"] = [refused(error_type="RPCError")]
    await run_to_end(campaign_id)

    await run_to_end(campaign_id)

    assert telegram.texts()[0] in {"Hello a", "Hello b"}
    assert telegram.texts()[1:] == ["Second"]
