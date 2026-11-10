"""What the round and rest events name, and what the board shows of a rest already over."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.repositories import chat_broadcast as repository
from schemas.chat_broadcast_records import TargetRecord
from services import chat_broadcast as service
from services.chat_broadcast import _moves, _schedule, _send
from tests.services.chat_broadcast.layout import chat, setup

if TYPE_CHECKING:
    from tests.services.chat_broadcast.fakes import Clock

_INVITE = "https://t.me/+SecretInviteHash123"


def _capture(
    monkeypatch: pytest.MonkeyPatch, *modules: object
) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []

    async def log_event(
        _level: str, event: str, *, extra: dict[str, object] | None = None, **_fields: object
    ) -> None:
        events.append((event, dict(extra or {})))

    for module in modules:
        monkeypatch.setattr(module, "log_event", log_event)
    return events


async def _invite_chat(campaign_id: str, key: str) -> TargetRecord:
    await repository.update_target(campaign_id, key, kind="invite", raw=_INVITE, username=None)
    target = await repository.fetch_target(campaign_id, key)
    assert target is not None
    return target


def test_a_chat_is_named_by_its_handle_or_id_and_never_by_its_invite_link() -> None:
    base = {"chat_key": "k", "raw": _INVITE, "kind": "invite", "position": 3}

    def named(**fields: object) -> str:
        values = {"state": "queued", "round": 1, "step_index": 0, "updated_unix": 1} | base | fields
        return _moves.chat_ref(TargetRecord.model_validate(values))

    assert named(kind="public", username="alpha", raw="t.me/alpha") == "@alpha"
    assert named(kind="own", username="grp", peer_id=7) == "@grp"
    assert named(kind="own", peer_id=7, raw="Some title") == "id:7"
    assert named(kind="folder", peer_id=9) == "id:9"
    assert named() == "#3"
    assert "Secret" not in named(peer_id=11)


@pytest.mark.asyncio
async def test_a_chat_that_opens_its_next_round_is_logged_without_its_invite_link(
    clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = _capture(monkeypatch, _schedule)
    campaign_id, ctx = await setup(
        ("a1",), [chat("x", "a1", "a1", "round_done", next_action_unix=clock.now() - 1)]
    )
    await _invite_chat(campaign_id, "x")

    await _schedule.tick(ctx)

    [(_event, extra)] = [e for e in events if e[0] == "chat_broadcast_round_started"]
    assert extra == {"campaign_id": campaign_id, "chat": "id:100", "round": 2}


@pytest.mark.asyncio
async def test_a_chat_that_goes_to_rest_is_logged_without_its_invite_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = _capture(monkeypatch, _send)
    campaign_id, ctx = await setup(("a1",), [chat("x", "a1", "a1", "writing")])
    target = await _invite_chat(campaign_id, "x")

    await _send._chain_played(ctx, target)

    [(_event, extra)] = [e for e in events if e[0] == "chat_broadcast_rest_started"]
    assert extra["chat"] == "id:100"
    assert "Secret" not in repr(events)


@pytest.mark.asyncio
async def test_the_board_shows_no_countdown_for_a_rest_that_is_already_over(
    clock: Clock,
) -> None:
    past = clock.now() - 5
    campaign_id, _ctx = await setup(
        ("a1", "a2"),
        [
            chat("x", "a1", "a1", "round_done", next_action_unix=past),
            chat("y", "a2", "a2", "round_done", next_action_unix=past - 60),
        ],
    )

    board = await service.load_board(campaign_id)

    assert board is not None
    # Every chat rests, but their time is up and only the scheduler has to open the round.
    assert board.phase == "resting"
    assert board.campaign.rest_until is None


@pytest.mark.asyncio
async def test_the_board_counts_down_to_the_first_rest_to_end(clock: Clock) -> None:
    campaign_id, _ctx = await setup(
        ("a1", "a2"),
        [
            chat("x", "a1", "a1", "round_done", next_action_unix=clock.now() + 900),
            chat("y", "a2", "a2", "round_done", next_action_unix=clock.now() + 300),
        ],
    )

    board = await service.load_board(campaign_id)

    assert board is not None
    assert board.campaign.rest_until is not None
    assert int(board.campaign.rest_until.timestamp()) == clock.now() + 300
