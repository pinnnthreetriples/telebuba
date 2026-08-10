"""A pair the guardian bot blocks gets ONE more solve, fired by the sweep — then nothing.

The captcha-blocked triple ``(joined=1, captcha_passed=0, ready=0)`` matches none of
``_join_and_classify``'s guards, so every onboarding trigger already re-ran the solver on
it, unbounded and untimed. ``_captcha_retry.review_captcha_blocked`` rides the deletion
sweep and bounds it: one authorised re-solve per pair, stamped as the poke goes out.

This file covers the AUTHORISATION half — who is picked up, who is not, what the stamp
stops, and what puts it back: the budget is one retry per EPISODE, so a pair that got past
the bot and comments again starts over, while a ready row nobody proved buys nothing. The
give-up and the channel drop live in ``test_captcha_give_up.py`` (test file cap); it is
asserted here only where it is the observable end of a budget claim.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.db import (  # type: ignore[attr-defined]
    _get_engine,
    assign_account_to_campaign,
    bump_channel_pause,
    create_account,
    create_campaign,
    fetch_comment,
    fetch_readiness,
    insert_challenge,
    link_channel_to_campaign,
    list_captcha_blocked_readiness,
    list_recent_logs,
    upsert_readiness,
)
from core.repositories.neurocomment import set_campaign_account_channels
from schemas.accounts import AccountCreate
from schemas.challenge import ChallengeInsert
from schemas.neurocomment import CampaignCreate, NeurocommentReadiness
from schemas.telegram_actions import NewPostEvent
from services.neurocomment import _captcha_retry, _runtime, _seams, engine
from tests.services.neurocomment.engine_support import _CommentStub, _patch_io
from tests.services.neurocomment.onboarding_support import _ok_action

if TYPE_CHECKING:
    from schemas.logs import LogEntry
    from schemas.telegram_actions import ActionResult, TelegramAction

pytestmark = pytest.mark.usefixtures("isolate_onboarding")

_CHANNEL = "@chan"
# Wide enough to swallow the shipped 48h window in the field-for-field probe below, where
# the challenge half must never be what decides a case.
_ANCIENT = "2000-01-01T00:00:00+00:00"


async def _campaign(*accounts: str, channel: str = _CHANNEL) -> str:
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    await link_channel_to_campaign(campaign.campaign_id, channel)
    for account_id in accounts:
        await create_account(AccountCreate(account_id=account_id, session_name=account_id))
        await assign_account_to_campaign(campaign.campaign_id, account_id)
    return campaign.campaign_id


async def _block(account_id: str, *, channel: str = _CHANNEL, challenge: bool = True) -> None:
    """Leave the pair exactly as a lost bot challenge does: in the group, unable to speak.

    ``challenge=False`` writes the SAME readiness triple with no challenge row behind it —
    which is what ``_classify``'s ``_GATE_ERRORS`` branch (an admin mute) leaves — so the
    tests can prove the challenges table is what tells the two apart.
    """
    await upsert_readiness(account_id, channel, joined=True, captcha_passed=False, ready=False)
    if challenge:
        await insert_challenge(
            ChallengeInsert(
                challenge_hash=f"h-{account_id}-{channel}",
                account_id=account_id,
                channel=channel,
                raw_text="press the duck",
                outcome="give_up",
            ),
        )


def _backdate_challenges(*, hours: float) -> None:
    """Age every challenge row, standing in for the 48h window actually running out.

    ``insert_challenge`` writes the wall clock, so a review called with a synthetic ``now``
    still measures against a row from a second ago — walking the timeline means moving the
    rows, not only the argument.
    """
    stamp = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    with _get_engine().begin() as connection:
        connection.exec_driver_sql(
            "UPDATE neurocomment_challenges SET decided_at = ?",
            (stamp,),
        )


def _pokes(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Capture the onboarding pokes instead of spawning a real pass."""
    triggered: list[object] = []
    monkeypatch.setattr(_runtime, "_ensure_onboarding_running", triggered.append)
    return triggered


async def _events(event: str) -> list[LogEntry]:
    return [entry for entry in await list_recent_logs(limit=100) if entry.event == event]


async def _retry_stamp(account_id: str, *, channel: str = _CHANNEL) -> str | None:
    row = await fetch_readiness(account_id, channel)
    assert row is not None
    return row.captcha_retry_at


async def _gave_up(account_id: str, *, channel: str = _CHANNEL) -> bool:
    row = await fetch_readiness(account_id, channel)
    assert row is not None
    return row.captcha_gave_up


# --------------------------------------------------------------------------- #
# Who gets the one retry.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_captcha_blocked_pair_is_stamped_and_pokes_onboarding_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole authorisation, in one tick: stamp, poke, and a log that names the budget."""
    await _campaign("acc-1")
    await _block("acc-1")
    triggered = _pokes(monkeypatch)

    await _captcha_retry.review_captcha_blocked(datetime.now(UTC))

    assert len(triggered) == 1
    assert await _retry_stamp("acc-1") is not None
    [entry] = await _events("neurocomment_captcha_retry")
    # "2/2", not "1/2": the pair's FIRST attempt was the onboarding solve this rule never
    # saw, so the one it authorises here is the last one.
    assert entry.extra.get("reason") == "2/2"
    assert entry.extra.get("channel") == _CHANNEL
    assert entry.account_id == "acc-1"


@pytest.mark.asyncio
async def test_a_gate_error_pair_with_no_challenge_row_is_left_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The discriminator: the same triple, written by an admin mute, is not a captcha.

    ``_classify``'s ``_GATE_ERRORS`` branch writes ``(joined=1, captcha_passed=0, ready=0)``
    for a ChatWriteForbidden/ChatGuestSendForbidden — a moderator block no solver can pass.
    Retrying it would spend a join RPC and a solver round on a wall that is not a captcha,
    and then give up on a pair for losing a fight it was never in.
    """
    await _campaign("acc-1")
    await _block("acc-1", challenge=False)
    triggered = _pokes(monkeypatch)

    await _captcha_retry.review_captcha_blocked(datetime.now(UTC))

    assert triggered == []
    assert await _retry_stamp("acc-1") is None


@pytest.mark.asyncio
async def test_a_muted_pair_sharing_a_channel_with_a_captcha_pair_is_left_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The case the single-account guard above could not see, and the auditor's reproduction.

    A mute alone selects no channel at all, so a one-account channel was safe by accident.
    Put the same muted pair on a channel where ANOTHER account really did lose a captcha and
    the bulk read used to widen from the blocked pairs to every row of their channels — so
    the muted sibling was stamped, retried, marked ``captcha_gave_up`` and walked out of a
    group it had never fought a captcha in. Both halves of the rule now hold per row: the
    readiness triple AND a failed challenge of this pair's own.
    """
    left: list[str] = []

    async def _record_leave(account_id: str, action: TelegramAction) -> ActionResult:
        left.append(account_id)
        return await _ok_action(account_id, action)

    await _campaign("acc-1", "acc-2")
    await _block("acc-1")  # lost a real captcha
    await _block("acc-2", challenge=False)  # admin mute: identical triple, no challenge row
    monkeypatch.setattr(_seams, "execute", _record_leave)
    triggered = _pokes(monkeypatch)
    now = datetime.now(UTC)

    await _captcha_retry.review_captcha_blocked(now)

    assert await _retry_stamp("acc-1") is not None
    assert await _retry_stamp("acc-2") is None

    # The authorised re-solve comes back and loses again, so acc-1 is retired. acc-2 must
    # not ride along on a verdict about somebody else's captcha.
    await _block("acc-1")
    await _captcha_retry.review_captcha_blocked(now + timedelta(minutes=5))

    assert await _gave_up("acc-1") is True
    assert await _gave_up("acc-2") is False
    assert left == ["acc-1"]
    assert len(triggered) == 1


@pytest.mark.asyncio
async def test_a_failure_older_than_the_window_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The challenges table is append-only and kept 90 days, so the read must be bounded.

    A pair whose only failure predates the rule's whole 48h timeline belongs to a settled
    episode; handing it a fresh retry would make an old row a permanent nag.
    """
    await _campaign("acc-1")
    await _block("acc-1")
    _backdate_challenges(hours=49)
    triggered = _pokes(monkeypatch)

    await _captcha_retry.review_captcha_blocked(datetime.now(UTC))

    assert triggered == []
    assert await _retry_stamp("acc-1") is None


@pytest.mark.asyncio
async def test_a_stamped_pair_is_not_stamped_or_poked_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The budget is ONE retry, and the stamp is what holds it to one.

    The sweep ticks every five minutes; without the stamp the second tick would re-authorise
    the same pair, which is the unbounded loop this rule exists to end. The pair is still
    blocked and still inside its window here, so nothing else may happen either.
    """
    await _campaign("acc-1")
    await _block("acc-1")
    triggered = _pokes(monkeypatch)
    now = datetime.now(UTC)

    await _captcha_retry.review_captcha_blocked(now)
    first_stamp = await _retry_stamp("acc-1")

    await _captcha_retry.review_captcha_blocked(now + timedelta(minutes=5))

    assert len(triggered) == 1
    assert await _retry_stamp("acc-1") == first_stamp
    assert len(await _events("neurocomment_captcha_retry")) == 1


@pytest.mark.asyncio
async def test_a_paused_channel_is_sat_out_and_resumes_after_the_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A #147 pause refuses every join, so the poke would reach nothing and burn the budget.

    ``_join_and_classify`` returns ``channel_paused`` BEFORE any join RPC, so a stamp
    written during the window is spent against a channel nobody could try — the whole
    budget, since there is only one. Deferred, never waived: the first tick after the
    deadline picks the timeline up where it left off.
    """
    now = datetime.now(UTC)
    await _campaign("acc-1")
    await _block("acc-1")
    await bump_channel_pause(_CHANNEL, (now + timedelta(hours=24)).isoformat())
    triggered = _pokes(monkeypatch)

    await _captcha_retry.review_captcha_blocked(now)
    assert triggered == []
    assert await _retry_stamp("acc-1") is None

    await _captcha_retry.review_captcha_blocked(now + timedelta(hours=25))
    assert len(triggered) == 1
    assert await _retry_stamp("acc-1") is not None


@pytest.mark.asyncio
async def test_a_pair_no_onboarding_pass_would_reach_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pinned elsewhere or off the campaign entirely: nobody's pair to retry.

    An onboarding pass walks the campaign's SERVING accounts, pin-aware. Stamping a row it
    never visits would spend the pair's only retry on a solve that never runs, and the next
    tick would then give up on it and take it out of a chat it is still fine in.
    """
    campaign_id = await _campaign("acc-1", "acc-2")
    await link_channel_to_campaign(campaign_id, "@elsewhere")
    await _block("acc-1")
    await _block("acc-2")
    # acc-1 is pinned to another of the campaign's channels, so it does not serve this one.
    await set_campaign_account_channels(campaign_id, "acc-1", ["@elsewhere"])
    triggered = _pokes(monkeypatch)

    await _captcha_retry.review_captcha_blocked(datetime.now(UTC))

    assert await _retry_stamp("acc-1") is None
    # acc-2 still serves the channel, so it IS retried — the exclusion is per pair, not a
    # blanket refusal of the channel.
    assert await _retry_stamp("acc-2") is not None
    assert len(triggered) == 1


# --------------------------------------------------------------------------- #
# One retry per EPISODE: what refunds the budget, and what only looks like it.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.usefixtures("isolate_engine")
async def test_a_delivered_comment_refunds_the_retry_for_the_next_episode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stamp is one EPISODE's authorisation, not a mark on the pair for life.

    A pair whose re-solve worked comments for weeks and then meets a new guardian bot. With
    the stamp left standing that pair is ``retry_owed=False`` and ``retry_spent=True`` on the
    very FIRST tick of the new episode, so it is retired and walked out of the group without
    a single retry — and the ``2/2`` line written on that same tick claims one was granted.
    The engine fixture rides along because the refund hangs off a comment Telegram actually
    accepted, which is the only evidence in the system that the wall is really gone.
    """
    await _campaign("acc-1")
    await _block("acc-1")
    triggered = _pokes(monkeypatch)
    now = datetime.now(UTC)

    await _captcha_retry.review_captcha_blocked(now)
    assert await _retry_stamp("acc-1") is not None

    # The authorised re-solve passes: onboarding writes the ready row, and the pair gets a
    # comment past the bot. THAT is the recovery — the row alone would not be (see below).
    await upsert_readiness("acc-1", _CHANNEL, joined=True, captcha_passed=True, ready=True)
    _patch_io(monkeypatch, comment=_CommentStub(status="ok"))
    await engine.handle_new_post(NewPostEvent(channel=_CHANNEL, post_id=1, text="hello world"))
    delivered = await fetch_comment(_CHANNEL, 1)
    assert delivered is not None
    assert delivered.status == "posted"
    assert await _retry_stamp("acc-1") is None

    # A new episode: another bot, the same pair, and a budget that has to start over.
    await _block("acc-1")
    await _captcha_retry.review_captcha_blocked(now + timedelta(minutes=5))

    assert await _retry_stamp("acc-1") is not None
    assert await _gave_up("acc-1") is False
    assert len(triggered) == 2
    assert len(await _events("neurocomment_captcha_retry")) == 2


@pytest.mark.asyncio
async def test_an_optimistic_ready_row_alone_does_not_refund_the_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The boundary, and the whole reason the refund is not written where ready is.

    ``_solve_and_record`` writes ``ready`` optimistically for ``no_challenge`` too — the
    solver simply saw nothing in its wait window — and the engine only finds out on the first
    comment. Refunding on that row would close a loop with no bound: block, stamp, poke, a
    ready row nobody proved, refund, block again, five minutes later the same thing, and the
    pair never reaches the terminal state this rule exists to give it. So the ready row buys
    nothing, and the pair that produced one is retired exactly like any other spent retry —
    the ``already_participant`` line ``_rejoin`` draws, drawn here.
    """
    await _campaign("acc-1")
    await _block("acc-1")
    triggered = _pokes(monkeypatch)
    now = datetime.now(UTC)

    await _captcha_retry.review_captcha_blocked(now)
    stamp = await _retry_stamp("acc-1")

    # The poked pass reports the pair comment-able...
    await upsert_readiness("acc-1", _CHANNEL, joined=True, captcha_passed=True, ready=True)
    # ...and the first comment is refused, so ``_outcomes``' gate branch writes the wall back.
    await _block("acc-1")

    await _captcha_retry.review_captcha_blocked(now + timedelta(minutes=5))

    assert await _retry_stamp("acc-1") == stamp
    assert await _gave_up("acc-1") is True
    assert len(triggered) == 1
    assert len(await _events("neurocomment_captcha_retry")) == 1


@pytest.mark.asyncio
async def test_a_pair_that_never_recovers_still_gets_exactly_one_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one-shot contract, end to end: authorise once, retire, and never come back.

    The refund must not weaken this. A pair that answers its re-solve and loses again has
    spent the budget, and no tick after that may hand it another — the unbounded re-solve
    loop is what the rule was written to end.
    """
    await _campaign("acc-1")
    await _block("acc-1")
    triggered = _pokes(monkeypatch)
    now = datetime.now(UTC)

    await _captcha_retry.review_captcha_blocked(now)
    await _block("acc-1")  # the re-solve came back and the bot still will not let it speak
    await _captcha_retry.review_captcha_blocked(now + timedelta(minutes=5))
    await _captcha_retry.review_captcha_blocked(now + timedelta(minutes=10))

    assert await _gave_up("acc-1") is True
    assert len(triggered) == 1
    assert len(await _events("neurocomment_captcha_retry")) == 1


# --------------------------------------------------------------------------- #
# The two predicates, pinned to each other and to the sweep's promise.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_captcha_blocked_matches_the_sql_predicate_field_for_field() -> None:
    """``_captcha_retry.captcha_blocked`` and ``_captcha_giveup._CAPTCHA_BLOCKED`` are one rule.

    Two spellings of six flags, in two languages, read by two layers: the bulk read decides
    which pairs the review ever sees and the Python predicate decides which of them it acts
    on, so a drift on ANY field silently changes the rule. Probed exhaustively rather than
    by example — every combination gets its own channel, so the read returning that channel
    is exactly "SQL says blocked".
    """
    flags = ("joined", "captcha_passed", "ready", "banned", "human_skipped", "captcha_gave_up")
    expected: set[str] = set()
    for index, combination in enumerate(itertools.product((False, True), repeat=len(flags))):
        row = dict(zip(flags, combination, strict=True))
        channel = f"@c{index}"
        account_id = f"acc-{index}"
        await _campaign(account_id, channel=channel)
        await upsert_readiness(
            account_id,
            channel,
            joined=row["joined"],
            captcha_passed=row["captcha_passed"],
            ready=row["ready"],
        )
        with _get_engine().begin() as connection:
            connection.exec_driver_sql(
                "UPDATE neurocomment_readiness SET banned = ?, human_skipped = ?, "
                "captcha_gave_up = ? WHERE account_id = ? AND channel = ?",
                (
                    int(row["banned"]),
                    int(row["human_skipped"]),
                    int(row["captcha_gave_up"]),
                    account_id,
                    channel,
                ),
            )
        await insert_challenge(
            ChallengeInsert(
                challenge_hash=f"h{index}",
                account_id=account_id,
                channel=channel,
                raw_text="x",
                outcome="give_up",
            ),
        )
        stored = await fetch_readiness(account_id, channel)
        assert stored is not None
        if _captcha_retry.captcha_blocked(stored):
            expected.add(channel)

    listed = {row.channel for row in (await list_captcha_blocked_readiness(_ANCIENT)).readiness}
    assert listed == expected
    # Guard against the degenerate agreement of two predicates that both match nothing.
    assert expected


@pytest.mark.asyncio
async def test_a_raising_bulk_read_is_logged_and_does_not_abort_the_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The review owns one pass of a loop that must never die — so it swallows and reports.

    Its own guard, not the sweep loop's: this one keeps the pass's own siblings inside the
    same tick running, and names the fault so a review that has stopped working can never
    be silent.
    """

    async def boom(_since: str) -> None:
        msg = "captcha review boom"
        raise RuntimeError(msg)

    monkeypatch.setattr(_captcha_retry, "list_captcha_blocked_readiness", boom)

    await _captcha_retry.review_captcha_blocked(datetime.now(UTC))

    [entry] = await _events("neurocomment_captcha_review_failed")
    assert entry.extra.get("error_type") == "RuntimeError"


def test_the_readiness_model_carries_the_two_new_columns() -> None:
    """A row from before migration #49 must read as "not asked, not terminal"."""
    row = NeurocommentReadiness(
        account_id="a",
        channel=_CHANNEL,
        joined=True,
        captcha_passed=False,
        ready=False,
        checked_at="2026-01-01T00:00:00+00:00",
    )
    assert (row.captcha_retry_at, row.captcha_gave_up) == (None, False)
