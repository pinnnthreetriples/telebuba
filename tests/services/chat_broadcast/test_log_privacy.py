"""A chat reached by a private invite or a folder link never reaches the log or the history.

The link is the way INTO the chat: whoever reads the operator's logs would hold it. Each
event names the chat by its handle, its id or its position instead, and the journal's own
key (``chat_key``, a database identity that never leaves the database) is left as it is.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

import pytest
from loguru import logger as loguru_logger

from core.repositories import chat_broadcast as repository
from core.repositories.logs import list_recent_logs
from schemas.chat_broadcast import ChatBroadcastTargetAction
from schemas.chat_broadcast_records import JournalKey, TargetRecord
from schemas.telegram_actions import BroadcastSendMessage
from services import chat_broadcast as service
from services.chat_broadcast import _join, _moves, _rotation, _send, targets
from services.chat_broadcast._context import load_context
from tests.services.chat_broadcast.fakes import refused, run_to_end, seed
from tests.services.chat_broadcast.layout import chat, setup

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tests.services.chat_broadcast.fakes import Clock, FakeTelegram

_HASH = "PrivateHash0123"
_LINK = f"https://t.me/+{_HASH}"
_SLUG = "FolderSlug0123"
_FOLDER = f"t.me/addlist/{_SLUG}"
# What a chat may be called in the log: a handle, an id, a position, or a fingerprint.
_SAFE = re.compile(r"^(@\w+|id:\d+|#\d+|invite:[0-9a-f]{6}|folder:[0-9a-f]{6})$")


@pytest.fixture
def written(caplog: pytest.LogCaptureFixture) -> Iterator[list[str]]:
    """Everything the run said through loguru and the stdlib loggers."""
    lines: list[str] = []
    sink = loguru_logger.add(lambda message: lines.append(str(message)), level="DEBUG")
    caplog.set_level(logging.DEBUG)
    yield lines
    loguru_logger.remove(sink)
    lines.extend(record.getMessage() for record in caplog.records)


async def _assert_clean(campaign_id: str, written: list[str], *events: str) -> None:
    """``events`` were logged, and nothing anywhere holds the link or the slug."""
    logs = await list_recent_logs(limit=1000)
    assert set(events) <= {row.event for row in logs}
    for row in logs:
        named = row.extra.get("chat")
        if named is not None:
            assert _SAFE.match(str(named)), (row.event, named)
    stored = repr([(row.event, row.extra, row.account_id) for row in logs])
    # The history's ``chat_key`` is the chat's database identity and stays; what the rows
    # SAY about the chat (kind, detail, account) must hold nothing of it.
    stored += repr(
        [(e.kind, e.detail, e.account_id) for e in await repository.list_events(campaign_id)]
    )
    for secret in (_HASH, _SLUG):
        assert secret not in stored
        assert all(secret not in line for line in written)


async def _invite_chat(campaign_id: str, key: str, raw: str = _LINK) -> TargetRecord:
    await repository.update_target(campaign_id, key, kind="invite", raw=raw, username=None)
    target = await repository.fetch_target(campaign_id, key)
    assert target is not None
    return target


@pytest.mark.asyncio
async def test_a_private_chat_that_is_joined_and_written_leaves_no_link_in_the_log(
    telegram: FakeTelegram, written: list[str]
) -> None:
    campaign_id = await seed(accounts=("a1",), targets=(_LINK,), messages=("Hi",))

    await run_to_end(campaign_id)

    assert telegram.joins == [("a1", f"+{_HASH}")]
    assert telegram.texts() == ["Hi"]
    await _assert_clean(
        campaign_id, written, "chat_broadcast_chat_joined", "chat_broadcast_message_sent"
    )
    # The database identity of the chat is not a log line and stays what it was.
    [target] = await repository.list_targets(campaign_id)
    assert target.chat_key == f"+{_HASH}"


@pytest.mark.asyncio
async def test_a_join_request_and_its_approval_leave_no_link_in_the_log(
    telegram: FakeTelegram, clock: Clock, written: list[str]
) -> None:
    campaign_id = await seed(accounts=("a1",), targets=(_LINK,), messages=("Hi",))
    token = f"+{_HASH}"
    telegram.join_answers[("a1", token)] = [refused(error_type="InviteRequestSentError")]
    telegram.hidden.add(token)
    telegram.approve_at[token] = clock.now() + 3 * 3600

    await run_to_end(campaign_id)

    await _assert_clean(
        campaign_id,
        written,
        "chat_broadcast_join_requested",
        "chat_broadcast_join_approved",
        "chat_broadcast_chat_joined",
    )


@pytest.mark.asyncio
async def test_a_failed_captcha_hands_the_solver_and_the_log_no_link(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch, written: list[str]
) -> None:
    named: list[str] = []

    async def solve(_account_id: str, chat_name: str, _group_id: int) -> str:
        named.append(chat_name)
        return "give_up"

    monkeypatch.setattr("services.chat_broadcast._seams.solve_challenge", solve)
    campaign_id = await seed(accounts=("a1",), targets=(_LINK,), messages=("Hi",))

    await run_to_end(campaign_id)

    # The solver writes this name into neurocomment's audit row and feed lines.
    [name] = named
    assert _SAFE.match(name)
    assert telegram.sent == []
    await _assert_clean(campaign_id, written, "chat_broadcast_chat_skipped")


@pytest.mark.asyncio
async def test_a_folder_that_cannot_be_opened_is_logged_without_its_slug(
    telegram: FakeTelegram, written: list[str]
) -> None:
    campaign_id = await seed(accounts=("a1",), targets=(_FOLDER,), messages=("Hi",))
    ctx = await load_context(campaign_id, "run-1")
    assert ctx is not None

    seeds = await targets.materialize(campaign_id, ctx.settings, ["a1"])

    assert seeds == []
    assert telegram.joins == []
    await _assert_clean(campaign_id, written, "chat_broadcast_folder_unavailable")
    [row] = [r for r in await list_recent_logs(limit=50) if "folder" in r.event]
    assert str(row.extra["chat"]).startswith("folder:")


@pytest.mark.asyncio
async def test_a_join_held_back_by_the_daily_cap_leaves_no_link_in_the_log(
    written: list[str],
) -> None:
    campaign_id, ctx = await setup(("a1",), [chat("x", "a1", None, "queued")])
    target = await _invite_chat(campaign_id, "x")

    assert await _join._cap_reached(ctx, "a1", target) == "idle"

    await _assert_clean(campaign_id, written, "chat_broadcast_join_daily_cap")


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["banned", "muted"])
async def test_a_chat_that_refuses_an_account_is_rerouted_without_its_link(
    written: list[str], reason: str
) -> None:
    campaign_id, ctx = await setup(("a1", "a2"), [chat("x", "a1", "a1", "writing")])
    target = await _invite_chat(campaign_id, "x")

    await _rotation.reroute(ctx, "a1", target, reason)  # ty: ignore[invalid-argument-type]

    event = (
        "chat_broadcast_chat_rerouted" if reason == "banned" else "chat_broadcast_muted_rerouted"
    )
    await _assert_clean(campaign_id, written, event)


@pytest.mark.asyncio
async def test_a_chat_that_fails_and_is_skipped_leaves_no_link_in_the_log(
    written: list[str],
) -> None:
    campaign_id, ctx = await setup(("a1",), [chat("x", "a1", "a1", "writing")])
    target = await _invite_chat(campaign_id, "x")

    assert not await _moves.chat_failed(ctx, "a1", target, "RPCError")

    await _assert_clean(
        campaign_id, written, "chat_broadcast_message_failed", "chat_broadcast_chat_skipped"
    )
    [skipped] = await repository.list_events(campaign_id)
    # The history keeps the chat's database key and a stable code, never the link.
    assert (skipped.chat_key, skipped.kind, skipped.detail) == ("x", "skipped", "error")


@pytest.mark.asyncio
async def test_a_chat_kept_from_the_board_leaves_no_link_in_the_log(written: list[str]) -> None:
    campaign_id, _ctx = await setup(("a1",), [chat("x", "a1", "a1", "queued")])
    await _invite_chat(campaign_id, "x")
    await repository.update_target(
        campaign_id, "x", state="skipped", skip_reason="deleted", message_deleted=1
    )
    await repository.set_status(campaign_id, "stopped")

    await service.act_on_target(campaign_id, ChatBroadcastTargetAction(chat_key="x", action="keep"))

    await _assert_clean(campaign_id, written, "chat_broadcast_chat_kept")


@pytest.mark.asyncio
async def test_a_chat_that_takes_no_photos_is_logged_without_its_link(
    telegram: FakeTelegram, written: list[str]
) -> None:
    campaign_id, ctx = await setup(("a1",), [chat("x", "a1", "a1", "writing")])
    target = await _invite_chat(campaign_id, "x")
    key = JournalKey(run_id=ctx.run_id, chat_key="x", round=1, step_index=0)
    action = BroadcastSendMessage(chat="100", text="caption", photo=b"png")

    result = await _send._without_photo(ctx, "a1", target, key, action)

    assert result.status == "ok"
    assert [s.photo for s in telegram.sent] == [False]
    await _assert_clean(campaign_id, written, "chat_broadcast_photo_dropped")
