"""A clean @SpamBot answer lifts a confirmed spam block, whoever asked.

The mark clears and the account is back to normal (DMs by the usual rules, no badge, no
attention row); a parked ``spam_blocked`` account goes idle — ready to warm, its wave
already ended by the stop — and a kept one stays where «Оставить» put it. The lift is a
compare-and-set on the mark, journalled as ``spam_block_lifted``.
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
from core.repositories.warming_spam_block import lift_spam_block_mark
from schemas.spam_status import SpamStatusKind, SpamStatusProbe, SpamStatusVerdict
from schemas.warming import WarmingStateRecord, WarmingStateWrite, spam_block_known
from services import _account_owner, spam_block_lift, spam_status, warming
from services.warming import _runtime, _spam_block
from tests.services.warming._support import _fake_loop, _seed_ready_account

_BLOCKED_AT = "2026-10-01T00:00:00+00:00"
_RUN = "run-1"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch: pytest.MonkeyPatch) -> object:
    monkeypatch.setattr(_runtime, "_warming_loop", _fake_loop)
    _account_owner.reset_for_tests()
    yield
    for task in list(warming._RUNTIME.values()):
        task.cancel()
    _account_owner.reset_for_tests()


def _verdict(status: SpamStatusKind, at: str | None = None) -> SpamStatusVerdict:
    checked = at or datetime.now(UTC).isoformat()
    return SpamStatusVerdict(account_id="acc-1", status=status, checked_at=checked)


async def _blocked(state: str = "spam_blocked", *, promoted: bool = False) -> None:
    await _seed_ready_account("acc-1")
    await save_warming_settings(
        inter_account_chat=True,
        reactions_enabled=False,
        enforce_readiness=True,
        gemini_api_key="key",
    )
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state=state,  # ty: ignore[invalid-argument-type]
            started_at=(datetime.now(UTC) - timedelta(days=5)).isoformat(),
            warmed_days=3,
            spam_blocked_at=_BLOCKED_AT,
            run_id=_RUN,
            next_run_at="2026-10-02T00:00:00+00:00",
            last_error="spam blocked",
            wave=1,
        ),
    )
    if promoted:
        await mark_promoted_to_nc("acc-1")
    await upsert_spam_status(_verdict("limited"))


async def _kinds() -> list[str]:
    return [event.kind for event in await list_lifecycle_events("acc-1")]


@pytest.mark.asyncio
async def test_a_clean_answer_puts_a_parked_account_back_in_ready_to_warm() -> None:
    await _blocked()
    clean = await upsert_spam_status(_verdict("clean"))

    lifted = await spam_block_lift.lift_spam_block("acc-1", clean)

    assert lifted is True
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert (record.state, record.spam_blocked_at, record.last_event) == (
        "idle",
        None,
        "spam_block_lifted",
    )
    assert (record.run_id, record.next_run_at, record.last_error) == (None, None, None)
    assert record.promoted_to_nc is False, "not graduated: the stop ended its wave"
    board = await warming.load_board()
    assert [card.account_id for card in board.idle] == ["acc-1"]
    (card,) = board.idle
    assert card.attention is None
    assert card.spam_blocked_at is None
    assert card.dm_status != "spam_blocked"
    assert board.summary.attention == 0
    [lift] = [e for e in await list_lifecycle_events("acc-1") if e.kind == "spam_block_lifted"]
    assert lift.extra == {"blocked_at": _BLOCKED_AT, "from_state": "spam_blocked"}
    events = [entry.event for entry in await list_recent_logs()]
    assert events.count("warming_spam_block_lifted") == 1


@pytest.mark.asyncio
async def test_a_kept_account_stays_on_the_warmed_card_without_its_mark() -> None:
    await _blocked(state="idle", promoted=True)

    assert await spam_block_lift.lift_spam_block("acc-1", _verdict("clean")) is True

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert (record.state, record.promoted_to_nc, record.spam_blocked_at) == ("idle", True, None)
    board = await warming.load_board()
    assert [(acc.account_id, acc.spam_blocked_at) for acc in board.warmed] == [("acc-1", None)]


@pytest.mark.asyncio
async def test_a_rewarming_kept_account_keeps_warming_and_gets_dms_back() -> None:
    await _blocked(state="sleeping")

    assert await spam_block_lift.lift_spam_block("acc-1", _verdict("clean")) is True

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert (record.state, record.run_id, record.spam_blocked_at) == ("sleeping", _RUN, None)
    assert spam_block_known(record) is False
    (card,) = (await warming.load_board()).warming
    assert card.dm_status != "spam_blocked"


@pytest.mark.parametrize(
    "verdict",
    [
        _verdict("limited"),
        _verdict("unknown"),
        # A clean answer read before the block was confirmed says nothing about it.
        _verdict("clean", "2026-09-30T00:00:00+00:00"),
    ],
)
@pytest.mark.asyncio
async def test_only_a_clean_answer_newer_than_the_block_lifts_it(
    verdict: SpamStatusVerdict,
) -> None:
    await _blocked()

    assert await spam_block_lift.lift_spam_block("acc-1", verdict) is False

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert (record.state, record.spam_blocked_at) == ("spam_blocked", _BLOCKED_AT)
    assert "spam_block_lifted" not in await _kinds()


@pytest.mark.asyncio
async def test_an_account_without_a_block_is_left_alone() -> None:
    await _seed_ready_account("acc-1")
    await upsert_warming_state(WarmingStateWrite(account_id="acc-1", state="active"))

    assert await spam_block_lift.lift_spam_block("acc-1", _verdict("clean")) is False
    assert await spam_block_lift.lift_spam_block("ghost", _verdict("clean")) is False


@pytest.mark.asyncio
async def test_the_lift_is_a_compare_and_set_on_the_mark() -> None:
    await _blocked()

    assert await lift_spam_block_mark("acc-1", "2026-10-05T00:00:00+00:00") is None
    assert await lift_spam_block_mark("acc-1", _BLOCKED_AT) == "spam_blocked"
    assert await lift_spam_block_mark("acc-1", _BLOCKED_AT) is None, "applies once"


@pytest.mark.asyncio
async def test_the_spam_check_itself_lifts_the_block_on_a_clean_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _blocked()

    async def probe(account_id: str) -> SpamStatusProbe:
        return SpamStatusProbe(account_id=account_id, reply_text="Good news, no limits")

    monkeypatch.setattr(spam_status, "check_spam_status", probe)

    verdict = await spam_status.refresh_spam_status("acc-1", force=True)

    assert verdict.status == "clean"
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert (record.state, record.spam_blocked_at) == ("idle", None)
    kinds = await _kinds()
    assert kinds.index("spambot_verdict") < kinds.index("spam_block_lifted")


@pytest.mark.asyncio
async def test_a_failed_lift_never_fails_the_spam_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _blocked()

    async def probe(account_id: str) -> SpamStatusProbe:
        return SpamStatusProbe(account_id=account_id, reply_text="Good news, no limits")

    async def explode(*_args: object) -> str | None:
        msg = "database is locked"
        raise RuntimeError(msg)

    monkeypatch.setattr(spam_status, "check_spam_status", probe)
    monkeypatch.setattr(spam_block_lift, "lift_spam_block_mark", explode)

    verdict = await spam_status.refresh_spam_status("acc-1", force=True)

    assert verdict.status == "clean"
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.spam_blocked_at == _BLOCKED_AT, "left for the survival pass to lift"
    monkeypatch.undo()
    # The stored clean answer is newer than the mark, so the pass lifts it without asking.
    assert await spam_block_lift.lift_from_stored_answer("acc-1") is True
    assert await spam_block_lift.lift_from_stored_answer("acc-1") is False


@pytest.mark.asyncio
async def test_clean_limited_clean_flapping_lifts_each_confirmed_block() -> None:
    await _blocked()
    assert await spam_block_lift.lift_spam_block("acc-1", _verdict("clean")) is True
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert spam_block_known(record) is False, "a new limited is news again"

    # Limited again, confirmed again by the quarantine re-check: a new, later mark.
    await upsert_warming_state(
        WarmingStateWrite(account_id="acc-1", state="quarantine", run_id=_RUN, quarantine_count=0)
    )
    stopped_at = datetime.now(UTC)
    record = await fetch_warming_state("acc-1")
    assert record is not None
    await _spam_block.stop_spam_blocked(record, stopped_at, 1, _RUN)
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert (record.state, record.spam_blocked_at) == ("spam_blocked", stopped_at.isoformat())

    # The first clean answer predates the new block: it lifts nothing.
    stale = _verdict("clean", (stopped_at - timedelta(minutes=1)).isoformat())
    assert await spam_block_lift.lift_spam_block("acc-1", stale) is False
    later = _verdict("clean", (stopped_at + timedelta(minutes=1)).isoformat())
    assert await spam_block_lift.lift_spam_block("acc-1", later) is True
    assert (await _kinds()).count("spam_block_lifted") == 2


@pytest.mark.asyncio
async def test_keep_racing_a_lift_is_refused_instead_of_re_marking() -> None:
    await _blocked()
    stale = await fetch_warming_state("acc-1")
    assert await spam_block_lift.lift_spam_block("acc-1", _verdict("clean")) is True

    with pytest.raises(_spam_block.NotSpamBlockedError):
        await _spam_block.keep_spam_blocked(stale, "acc-1")

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert (record.state, record.spam_blocked_at, record.promoted_to_nc) == ("idle", None, False)


def test_a_clean_reading_newer_than_the_mark_ends_the_known_block() -> None:
    marked = WarmingStateRecord(
        account_id="acc-1", state="sleeping", spam_blocked_at=_BLOCKED_AT, updated_at=_BLOCKED_AT
    )
    parked = marked.model_copy(update={"state": "spam_blocked"})
    newer = _verdict("clean", "2026-10-02T00:00:00+00:00")
    older = _verdict("clean", "2026-09-30T00:00:00+00:00")

    assert spam_block_known(marked) is True
    assert spam_block_known(marked, older) is True
    assert spam_block_known(marked, _verdict("limited")) is True
    assert spam_block_known(marked, newer) is False
    assert spam_block_known(parked, newer) is False
    assert spam_block_known(None, newer) is False
