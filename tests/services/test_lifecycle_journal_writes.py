"""Every lifecycle write point emits its journal row, and a journal fault breaks none."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.db import (
    create_account,
    fetch_account,
    save_warming_settings,
    update_account_from_session_check,
    upsert_warming_state,
)
from core.repositories.lifecycle import list_lifecycle_events
from schemas.accounts import AccountCheckRequest, AccountCreate
from schemas.spam_status import SpamStatusProbe, SpamStatusVerdict
from schemas.telegram_session import TelegramSessionCheckResult
from schemas.warming import (
    StartWarmingRequest,
    StopWarmingRequest,
    WarmingCycleResult,
    WarmingStateWrite,
)
from services import lifecycle_journal, spam_status, warming
from services.accounts import check_account_session, remove_account
from services.warming import _runtime, _seams, _start_state, _transitions
from tests.services.warming._support import _Recorder, _seed_ready_account, _set_settings

if TYPE_CHECKING:
    from schemas.telegram_session import SessionCheckStatus


async def _clean_spam_status(account_id: str, *, force: bool = False) -> SpamStatusVerdict:
    del force
    return SpamStatusVerdict(
        account_id=account_id, status="clean", checked_at=datetime.now(UTC).isoformat()
    )


async def _kinds(account_id: str = "acc-1") -> list[str]:
    return [event.kind for event in await list_lifecycle_events(account_id)]


@pytest.fixture
def broken_journal(monkeypatch: pytest.MonkeyPatch) -> None:
    async def explode(*_args: object, **_kwargs: object) -> None:
        msg = "journal down"
        raise RuntimeError(msg)

    monkeypatch.setattr(lifecycle_journal, "append_lifecycle_event", explode)


async def _start(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_loop(_account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
        await asyncio.sleep(3600)

    monkeypatch.setattr(_runtime, "_warming_loop", fake_loop)
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=False,
        gemini_api_key="",
    )
    await warming.start_warming(StartWarmingRequest(account_id="acc-1"))


@pytest.mark.asyncio
async def test_start_journals_a_new_stint_then_a_carried_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await create_account(AccountCreate(account_id="acc-1"))

    await _start(monkeypatch)
    await warming.start_warming(StartWarmingRequest(account_id="acc-1"))
    await warming.stop_warming(StopWarmingRequest(account_id="acc-1"))

    events = await list_lifecycle_events("acc-1")
    assert [(e.kind, e.extra) for e in events] == [
        ("warming_started", {"new_stint": True}),
        ("warming_started", {"new_stint": False}),
    ]
    assert events[0].stint_started_at is not None
    assert events[0].stint_started_at == events[1].stint_started_at


@pytest.mark.asyncio
@pytest.mark.usefixtures("broken_journal")
async def test_start_survives_a_broken_journal(monkeypatch: pytest.MonkeyPatch) -> None:
    await create_account(AccountCreate(account_id="acc-1"))

    await _start(monkeypatch)

    assert "acc-1" in warming._RUNTIME
    await warming.stop_warming(StopWarmingRequest(account_id="acc-1"))


@pytest.mark.asyncio
async def test_promote_and_handoff_are_journaled(monkeypatch: pytest.MonkeyPatch) -> None:
    async def refresh() -> None:
        return None

    monkeypatch.setattr(_runtime, "_refresh_dialogue_pairs", refresh)
    # The handoff now needs a clean @SpamBot answer; answer one without Telegram.
    monkeypatch.setattr(_seams, "_refresh_spam_status", _clean_spam_status)
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(WarmingStateWrite(account_id="acc-1", state="idle"))

    await warming.promote_to_neurocomment("acc-1")
    await warming.handoff_to_neurocomment("acc-1")

    assert await _kinds() == ["promoted", "handed_off"]


@pytest.mark.asyncio
async def test_reaching_the_target_is_journaled_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_seams, "execute", _Recorder().execute)
    await _seed_ready_account("acc-1")
    await _set_settings(chat=False, reactions=False, key="", enforce_readiness=False)
    old_start = (datetime.now(UTC) - timedelta(days=5)).isoformat()
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="sleeping",
            started_at=old_start,
            target_days=3,
            # The target counts worked days, not calendar days (stage 1.7).
            warmed_days=3,
        )
    )

    await warming.run_loop_iteration("acc-1")
    await warming.run_loop_iteration("acc-1")

    [event] = await list_lifecycle_events("acc-1")
    assert (event.kind, event.extra, event.stint_started_at) == (
        "warmed",
        {"warming_days": 3},
        old_start,
    )
    assert event.tg_user_id == 111


@pytest.mark.asyncio
async def test_a_peer_flood_quarantine_is_journaled() -> None:
    peer_flood = WarmingCycleResult(account_id="acc-1", status="peer_flood")

    _, _, state = await _transitions._calculate_next_run("acc-1", peer_flood, "normal", 40)

    assert state == "quarantine"
    assert await _kinds() == ["quarantined"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("broken_journal")
async def test_a_quarantine_survives_a_broken_journal() -> None:
    peer_flood = WarmingCycleResult(account_id="acc-1", status="peer_flood")

    _, _, state = await _transitions._calculate_next_run("acc-1", peer_flood, "normal", 40)

    assert state == "quarantine"


@pytest.mark.asyncio
async def test_a_spambot_answer_is_journaled(monkeypatch: pytest.MonkeyPatch) -> None:
    async def probe(account_id: str) -> SpamStatusProbe:
        return SpamStatusProbe(account_id=account_id, reply_text="limited until 2026-12-01.")

    monkeypatch.setattr(spam_status, "check_spam_status", probe)
    await create_account(AccountCreate(account_id="acc-1"))

    verdict = await spam_status.refresh_spam_status("acc-1", force=True)

    [event] = await list_lifecycle_events("acc-1")
    assert event.kind == "spambot_verdict"
    assert event.extra["status"] == verdict.status == "limited"


def _check_result(status: SessionCheckStatus) -> TelegramSessionCheckResult:
    return TelegramSessionCheckResult(
        account_id="acc-1",
        session_path="acc-1",
        status=status,
        is_temporary=False,
        user_id=5,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        ("alive", "unauthorized", ["session_dead"]),
        ("alive", "account_error", ["session_dead"]),
        ("unauthorized", "unauthorized", []),
        ("new", "unauthorized", []),
        ("alive", "network_error", []),
    ],
)
async def test_a_session_death_is_journaled_on_the_transition_only(
    monkeypatch: pytest.MonkeyPatch,
    before: SessionCheckStatus,
    after: SessionCheckStatus,
    expected: list[str],
) -> None:
    async def probe(_request: object) -> TelegramSessionCheckResult:
        return _check_result(after)

    monkeypatch.setattr("services.accounts.sessions.check_telegram_session", probe)
    await create_account(AccountCreate(account_id="acc-1"))
    if before != "new":
        await update_account_from_session_check(_check_result(before))

    await check_account_session(AccountCheckRequest(account_id="acc-1"))

    assert await _kinds() == expected


@pytest.mark.asyncio
async def test_a_delete_journals_the_reason_and_the_row_outlives_the_account() -> None:
    await create_account(AccountCreate(account_id="acc-1"))
    await create_account(AccountCreate(account_id="acc-2"))

    await remove_account("acc-1", reason="dead")
    await remove_account("acc-2")

    assert await fetch_account("acc-1") is None
    [dead] = await list_lifecycle_events("acc-1")
    assert dead.extra == {"reason": "dead", "reason_source": "operator"}
    [guessed] = await list_lifecycle_events("acc-2")
    assert guessed.extra == {"reason": "retired", "reason_source": "guess"}


@pytest.mark.asyncio
@pytest.mark.usefixtures("broken_journal")
async def test_a_delete_survives_a_broken_journal() -> None:
    await create_account(AccountCreate(account_id="acc-1"))

    await remove_account("acc-1", reason="retired")

    assert await fetch_account("acc-1") is None


@pytest.mark.asyncio
async def test_a_failed_delete_leaves_no_deleted_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """The account survives a failed cascade, so the journal must not call it deleted."""

    async def refuse(_account_id: str) -> None:
        msg = "database is locked"
        raise RuntimeError(msg)

    monkeypatch.setattr("services.accounts.lifecycle.delete_account", refuse)
    await create_account(AccountCreate(account_id="acc-1"))

    with pytest.raises(RuntimeError, match="locked"):
        await remove_account("acc-1", reason="dead")

    assert await fetch_account("acc-1") is not None
    assert await _kinds() == []


@pytest.mark.asyncio
async def test_a_start_journals_its_own_stint_not_a_later_restarts() -> None:
    # journal_start runs after the account lock is released; a concurrent restart may
    # already have stamped a newer stint, which this row must not pick up.
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1", state="active", started_at="2026-10-02T00:00:00+00:00"
        )
    )
    stint = _start_state.CarriedStint("2026-10-01T00:00:00+00:00", 7, "normal", None, fresh=True)

    await _start_state.journal_start("acc-1", None, stint, 4242)

    (event,) = await list_lifecycle_events("acc-1")
    assert event.stint_started_at == "2026-10-01T00:00:00+00:00"
    assert event.tg_user_id == 4242
