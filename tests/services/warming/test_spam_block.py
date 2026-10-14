"""The confirmed spam block: the stop, «Оставить», and that a known block never loops.

@SpamBot's ``limited`` is permanent. One quarantine re-check confirms it and warming stops
in ``spam_blocked``; the operator keeps or deletes the account. A kept account warms again
without DMs, and its ``limited`` answers are no news: no quarantine, no refused start or
hand-off, no dialogue partner waiting on it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.db import (
    fetch_warming_state,
    mark_promoted_to_nc,
    save_warming_settings,
    upsert_spam_status,
    upsert_warming_state,
)
from core.repositories.lifecycle import list_lifecycle_events
from core.repositories.logs import list_recent_logs
from schemas.spam_status import SpamStatusKind, SpamStatusVerdict
from schemas.warming import StartWarmingRequest, WarmingStateWrite
from services import _account_owner, dialogues, warming
from services.warming import _runtime, _seams, _spam_check
from tests.services.warming._support import _fake_loop, _seed_ready_account

_BLOCKED_AT = "2026-10-01T00:00:00+00:00"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch: pytest.MonkeyPatch) -> object:
    monkeypatch.setattr(_runtime, "_warming_loop", _fake_loop)
    _account_owner.reset_for_tests()
    yield
    for task in list(warming._RUNTIME.values()):
        task.cancel()
    _account_owner.reset_for_tests()


def _answer(monkeypatch: pytest.MonkeyPatch, status: SpamStatusKind) -> list[str]:
    asked: list[str] = []

    async def refresh(account_id: str, *, force: bool = False) -> SpamStatusVerdict:  # noqa: ARG001
        asked.append(account_id)
        return SpamStatusVerdict(
            account_id=account_id, status=status, checked_at=datetime.now(UTC).isoformat()
        )

    monkeypatch.setattr(_seams, "refresh_spam_status", refresh)
    monkeypatch.setattr(_seams, "_refresh_spam_status", refresh)
    return asked


async def _blocked(account_id: str = "acc-1", *, state: str = "spam_blocked") -> None:
    await _seed_ready_account(account_id)
    await save_warming_settings(
        inter_account_chat=True,
        reactions_enabled=False,
        enforce_readiness=True,
        gemini_api_key="key",
    )
    await upsert_warming_state(
        WarmingStateWrite(
            account_id=account_id,
            state=state,  # ty: ignore[invalid-argument-type]
            started_at=(datetime.now(UTC) - timedelta(days=5)).isoformat(),
            warmed_days=3,
            spam_blocked_at=_BLOCKED_AT,
            wave=1,
        ),
    )
    await upsert_spam_status(
        SpamStatusVerdict(
            account_id=account_id, status="limited", checked_at=datetime.now(UTC).isoformat()
        ),
    )


@pytest.mark.asyncio
async def test_keep_moves_the_account_to_idle_and_remembers_the_block() -> None:
    await _blocked()

    card = await warming.keep_spam_blocked("acc-1")
    again = await warming.keep_spam_blocked("acc-1")

    assert (card.state, card.spam_blocked_at) == ("idle", _BLOCKED_AT)
    assert card.attention is None, "kept: off the problem list"
    assert again.state == "idle"
    events = [entry.event for entry in await list_recent_logs()]
    assert events.count("warming_spam_block_kept") == 1


@pytest.mark.asyncio
async def test_keep_refuses_an_account_never_stopped_on_a_spam_block() -> None:
    await _seed_ready_account()
    await upsert_warming_state(WarmingStateWrite(account_id="acc-1", state="idle"))

    with pytest.raises(warming.NotSpamBlockedError):
        await warming.keep_spam_blocked("acc-1")
    with pytest.raises(warming.UnknownAccountError):
        await warming.keep_spam_blocked("ghost")


@pytest.mark.asyncio
async def test_the_stopped_account_waits_on_the_board_with_its_mark() -> None:
    await _blocked()

    board = await warming.load_board()

    (card,) = board.warming
    assert card.state == "spam_blocked"
    assert card.attention is not None
    assert card.attention.code == "spam_blocked"
    assert board.summary.attention == 1


@pytest.mark.asyncio
async def test_a_spam_blocked_row_is_never_restarted_by_reconcile() -> None:
    await _blocked()

    await warming.reconcile_warming_runtime()

    assert "acc-1" not in warming._RUNTIME
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.state == "spam_blocked"


@pytest.mark.asyncio
async def test_a_kept_account_starts_a_new_wave_without_dms_despite_limited() -> None:
    await _blocked(state="idle")

    card = await warming.start_warming_with_consent(StartWarmingRequest(account_id="acc-1"))

    record = await fetch_warming_state("acc-1")
    assert record is not None
    # Readiness is enforced, yet a known block's "limited" does not refuse the start.
    assert (record.state, record.wave, record.warmed_days) == ("active", 2, 0)
    assert record.spam_blocked_at == _BLOCKED_AT
    (board_card,) = (await warming.load_board()).warming
    assert (board_card.dm_allowed, board_card.dm_status) == (False, "spam_blocked")
    assert card.readiness is None or "spam limited" not in card.readiness.reasons


@pytest.mark.asyncio
async def test_a_start_from_the_stopped_state_is_a_fresh_wave() -> None:
    await _blocked()

    await warming.start_warming_with_consent(StartWarmingRequest(account_id="acc-1"))

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert (record.state, record.wave, record.warmed_days) == ("active", 2, 0)


@pytest.mark.asyncio
async def test_a_known_block_answering_limited_in_the_cycle_is_no_quarantine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _blocked(state="sleeping")
    asked = _answer(monkeypatch, "limited")
    record = await fetch_warming_state("acc-1")
    assert record is not None

    gated, was_asked = await _spam_check._spam_step(
        "acc-1", record, None, datetime.now(UTC), run_id=None
    )

    assert (gated, was_asked, asked) == (None, True, ["acc-1"])
    after = await fetch_warming_state("acc-1")
    assert after is not None
    assert after.state == "sleeping"


@pytest.mark.asyncio
async def test_a_kept_account_rests_out_a_peer_flood_and_goes_back_to_warming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _blocked(state="quarantine")
    _answer(monkeypatch, "limited")

    result = await warming.run_loop_iteration("acc-1")

    assert result.detail == "recovered"
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.state == "sleeping"


@pytest.mark.asyncio
async def test_a_kept_account_is_handed_off_with_its_known_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _blocked(state="idle")
    await mark_promoted_to_nc("acc-1")
    _answer(monkeypatch, "limited")

    card = await warming.handoff_to_neurocomment("acc-1")

    assert card.nc_handed_off is True
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.state == "idle", "never sent back into the quarantine ladder"


@pytest.mark.asyncio
async def test_the_spam_block_stop_journals_the_wave_end(monkeypatch: pytest.MonkeyPatch) -> None:
    await _seed_ready_account()
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="quarantine",
            started_at=(datetime.now(UTC) - timedelta(days=4)).isoformat(),
            warmed_days=2,
            wave=3,
        ),
    )
    _answer(monkeypatch, "limited")

    await warming.run_loop_iteration("acc-1")

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.state == "spam_blocked"
    stops = [e for e in await list_lifecycle_events("acc-1") if e.kind == "warming_stopped"]
    assert [e.extra for e in stops] == [{"reason": "spam_blocked", "wave": 3, "worked_days": 2}]
    events = [entry.event for entry in await list_recent_logs()]
    assert "warming_spam_blocked" in events


@pytest.mark.asyncio
async def test_a_spam_blocked_account_is_nobodys_dialogue_partner() -> None:
    await _blocked(state="sleeping")
    await _seed_ready_account("acc-2")
    await upsert_warming_state(WarmingStateWrite(account_id="acc-2", state="sleeping"))

    eligible = await dialogues._eligible_accounts()

    assert list(eligible) == ["acc-2"]
