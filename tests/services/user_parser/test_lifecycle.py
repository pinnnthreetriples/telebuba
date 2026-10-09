"""Stop, shutdown, restart and a crash: a run always settles and keeps what it collected."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core.repositories import user_parser as repository
from schemas.user_parser_run import UserParserRun
from services import _account_owner
from services.user_parser import (
    _seams,
    _state,
    get_run,
    reconcile_user_parser_on_startup,
    shutdown_user_parser_on_shutdown,
    start_run,
    stop_run,
)
from tests.services.user_parser.conftest import seed_accounts
from tests.services.user_parser.fakes import person
from tests.services.user_parser.helpers import kept, parser_request, settled

if TYPE_CHECKING:
    from pydantic import BaseModel

    from schemas.telegram_actions import TelegramReadAction
    from tests.services.user_parser.fakes import FakeTelegram


async def _stalled_after_first_source(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, asyncio.Event]:
    """A run that read its first source and now hangs on the second."""
    await seed_accounts("a1")
    telegram.members["first"] = [person(1), person(2)]
    telegram.members["second"] = [person(3)]
    reached = asyncio.Event()

    async def _hang_on_second(account_id: str, action: TelegramReadAction) -> BaseModel:
        if getattr(action, "chat", None) == "second":
            reached.set()
            await asyncio.Event().wait()
        return await telegram.execute_read(account_id, action)

    monkeypatch.setattr(_seams, "execute_read", _hang_on_second)
    outcome = await start_run(parser_request(sources=["@first", "@second"]))
    assert outcome.run_id is not None
    return outcome.run_id, reached


@pytest.mark.asyncio
async def test_stop_saves_what_was_collected(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id, reached = await _stalled_after_first_source(telegram, monkeypatch)
    await reached.wait()

    live = await get_run(run_id)
    assert live is not None
    assert (live.status, live.sources_done, live.collected_raw) == ("running", 1, 2)

    stopped = await stop_run(run_id)

    assert stopped is not None
    assert (stopped.status, stopped.kept) == ("stopped", 2)
    assert [(s.status, s.count) for s in stopped.sources] == [("ok", 2), ("pending", 0)]
    assert [u.user_id for u in await kept(run_id)] == [1, 2]
    assert _account_owner.owners() == {}
    # Stopping a settled run changes nothing.
    assert await stop_run(run_id) == stopped
    assert await stop_run("missing") is None


@pytest.mark.asyncio
async def test_shutdown_settles_a_run_as_interrupted(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id, reached = await _stalled_after_first_source(telegram, monkeypatch)
    await reached.wait()

    await shutdown_user_parser_on_shutdown()

    stored = await repository.fetch_run(run_id)
    assert stored is not None
    assert (stored.status, stored.kept) == ("interrupted", 2)
    assert _state.live(run_id) is None


@pytest.mark.asyncio
async def test_a_restart_marks_an_orphaned_run_interrupted(
    telegram: FakeTelegram,  # noqa: ARG001 - patches the seams
) -> None:
    orphan = UserParserRun(
        run_id="orphan", name="x", mode="members", status="running", created_at="2026-10-01"
    )
    await repository.create_run(orphan, parser_request())
    _account_owner.try_claim("a1", "user_parser", "orphan")

    await reconcile_user_parser_on_startup()
    await reconcile_user_parser_on_startup()

    stored = await repository.fetch_run("orphan")
    assert stored is not None
    assert (stored.status, stored.stop_reason) == ("interrupted", "restart")
    assert _account_owner.owners() == {}


@pytest.mark.asyncio
async def test_a_crash_inside_the_run_settles_it_failed(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed_accounts("a1")
    telegram.members["group"] = [person(1)]

    async def _boom(account_id: str, action: TelegramReadAction) -> BaseModel:  # noqa: ARG001
        raise ZeroDivisionError

    monkeypatch.setattr(_seams, "execute_read", _boom)
    outcome = await start_run(parser_request())
    await settled(outcome.run_id or "")

    stored = await repository.fetch_run(outcome.run_id or "")
    assert stored is not None
    assert (stored.status, stored.stop_reason) == ("failed", "ZeroDivisionError")
    assert _account_owner.owners() == {}


@pytest.mark.asyncio
async def test_an_unknown_run_reads_as_none(telegram: FakeTelegram) -> None:  # noqa: ARG001
    assert await get_run("missing") is None
