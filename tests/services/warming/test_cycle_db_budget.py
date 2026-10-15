"""How many SQL statements one warming cycle costs (Прогрев 2.0, item 5.1).

A spy on the engine counts every statement a whole ``run_loop_iteration`` sends — the
gates, the booking, the session itself and the settle — split into reads and writes,
each named by the repository function that sent it. Log rows are left out: they are
the activity feed, not the cycle's own state.

Before 5.1 this very cycle cost 43 reads and 11 writes: the account was read eight
times, the state row a dozen, the trust inputs twice, and every progress tick read
the row before writing it.
"""

from __future__ import annotations

import traceback
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import event

from core.db import _get_engine, upsert_warming_state
from core.repositories.spam_status import upsert_spam_status
from schemas.spam_status import SpamStatusVerdict
from schemas.warming import WarmingStateWrite
from services import warming
from services.warming import _seams
from services.warming._snapshot import current_snapshot
from tests.services.warming._support import _Recorder, _seed_ready_account, _set_settings

if TYPE_CHECKING:
    from collections.abc import Iterator

    from schemas.telegram_actions import ActionResult, TelegramAction

_WRITE_VERBS = frozenset({"INSERT", "UPDATE", "DELETE", "REPLACE"})
# Writes that follow what Telegram did rather than the cycle's bookkeeping: one per
# advance of the card's progress rail, one per channel joined.
_ACTIVITY_WRITERS = ("_store_cycle_progress", "_record_channel_joined")


class _StatementSpy:
    """Counts the statements the engine runs and the repository function behind each."""

    def __init__(self) -> None:
        self.reads: list[str] = []
        self.writes: list[str] = []

    def __call__(self, *args: object) -> None:
        statement = str(args[2]).lstrip().upper()
        if statement.startswith("INSERT INTO LOGS"):
            return
        verb = statement.split(None, 1)[0]
        # The statement runs in the repository's worker thread, so its innermost
        # repository frame names the call.
        frames = [f.name for f in traceback.extract_stack() if "repositories" in f.filename]
        caller = frames[-1] if frames else "?"
        if verb in _WRITE_VERBS:
            self.writes.append(caller)
        elif verb in {"SELECT", "WITH"}:
            self.reads.append(caller)


@pytest.fixture
def spy() -> Iterator[_StatementSpy]:
    engine = _get_engine()
    counter = _StatementSpy()
    event.listen(engine, "before_cursor_execute", counter)
    yield counter
    event.remove(engine, "before_cursor_execute", counter)


async def _seed_cycling_account() -> None:
    await _seed_ready_account("acc-1")
    await _set_settings(chat=False, reactions=True, key="", enforce_readiness=True)
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="sleeping",
            started_at=(datetime.now(UTC) - timedelta(days=4)).isoformat(),
            target_days=30,
        ),
    )
    # A fresh clean reading: the scheduled @SpamBot step has nothing to ask.
    await upsert_spam_status(
        SpamStatusVerdict(
            account_id="acc-1", status="clean", checked_at=datetime.now(UTC).isoformat()
        ),
    )


@pytest.mark.asyncio
async def test_a_cycle_reads_its_data_once(
    monkeypatch: pytest.MonkeyPatch, spy: _StatementSpy
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_cycling_account()
    spy.reads.clear()
    spy.writes.clear()

    result = await warming.run_loop_iteration("acc-1")

    # A full visit: online, a join, a read, a reaction, the stories glance, offline.
    assert result.status == "ok"
    assert recorder.types() == [
        "set_online",
        "join_channel",
        "read_channel",
        "react_to_post",
        "watch_peer_stories",
        "set_online",
    ]
    # Settings come from their own cache. Everything else is read once, and the settle
    # re-reads only the row it CAS-checks and the account a dead session may have
    # marked; the waiting-DM probe runs because this account may already send DMs.
    assert sorted(spy.reads) == sorted(
        [
            "_fetch_warming_state",
            "_get_spam_status",
            "_fetch_account",
            "_fetch_device_fingerprint",
            "_list_warming_flood_summaries",
            "_list_warming_channels",
            "_list_joined_channels",
            "_oldest_unreplied_for",
            "_fetch_warming_state",
            "_fetch_account",
        ],
    )
    bookkeeping = [w for w in spy.writes if w not in _ACTIVITY_WRITERS]
    # The booking, the settle, the day's totals and the worked day.
    assert sorted(bookkeeping) == sorted(
        [
            "_upsert_warming_state",
            "_upsert_warming_state",
            "_add_warming_daily_stats",
            "_count_warmed_day",
        ],
    )
    # The rail moves join → read → react → stories; set_online rides the booking.
    assert spy.writes.count("_store_cycle_progress") == 4
    assert spy.writes.count("_record_channel_joined") == 1


@pytest.mark.asyncio
async def test_the_snapshot_lives_for_one_iteration_only(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[bool] = []
    recorder = _Recorder()

    async def execute(account_id: str, action: TelegramAction) -> ActionResult:
        seen.append(current_snapshot(account_id) is not None)
        return await recorder.execute(account_id, action)

    monkeypatch.setattr(_seams, "execute", execute)
    await _seed_cycling_account()

    await warming.run_loop_iteration("acc-1")

    assert seen
    assert all(seen)
    assert current_snapshot("acc-1") is None
