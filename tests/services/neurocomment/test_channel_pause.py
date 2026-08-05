"""A channel that will not let us write gets ``channel_max_rounds`` rounds, then leaves (#147).

K consecutive write failures end a round: the channel is paused for a flat
``channel_pause_hours`` and its round counter goes up. Counter and deadline live on the
campaign link, NOT in memory — the live app restarted 7 times in three days, and a
multi-day rule built on module dicts never reached its last round. The final round pauses
like every other one and the channel is unlinked when THAT window runs out (t=48h at the
shipped budget, the same deadline its two sibling rules give) — but only once every serving
account has actually been tried there; a delivered comment clears both. Nothing on the post
path can deliver that last verdict, since a paused channel takes no posts, so the sweep
pass ``review_expired_pauses`` is what fires at the deadline.

The budget shipped as 4; it is 2 now, the operator's one rule for this and for ``_rejoin``.
The tests below that pin ``channel_max_rounds`` do so to keep a HOLD visible across several
rounds, which two cannot show; the one test that asserts the shipped verdict deliberately
does not pin it.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from core.config import settings
from core.db import (
    _get_engine,
    assign_account_to_campaign,
    create_account,
    fetch_active_campaign_for_channel,
    fetch_channel_paused_until,
    fetch_comment,
    link_channel_to_campaign,
    list_campaign_channels,
    list_recent_logs,
    stamp_rejoin_attempt,
    upsert_readiness,
)
from core.repositories.neurocomment import set_campaign_account_channels, set_campaign_status
from core.repositories.neurocomment._tables import _neurocomment_campaign_channels
from schemas.accounts import AccountCreate
from schemas.telegram_actions import NewPostEvent
from services.neurocomment import _channel_pause, _state, engine
from tests.services.neurocomment.engine_support import (
    _CommentStub,
    _make_campaign,
    _patch_io,
)

pytestmark = pytest.mark.usefixtures("isolate_engine")

_GATE = "ChatWriteForbiddenError"


def _one_failure_per_round(monkeypatch: pytest.MonkeyPatch) -> None:
    """K=1 so one gated post ends a round — the K counter itself is unit-tested."""
    monkeypatch.setattr(settings.neurocomment, "channel_challenge_backoff_min_failures", 1)


async def _rounds(campaign_id: str) -> int:
    links = (await list_campaign_channels(campaign_id)).links
    return links[0].pause_rounds if links else 0


async def _rewind_the_pause(age: timedelta = timedelta(seconds=1)) -> None:
    """Move the pause deadline ``age`` into the past, standing in for the window elapsing.

    The default is the window that JUST ran out — the only kind a verdict may be read off.
    A larger ``age`` is the row nobody was posting against: a stopped campaign, a long
    shutdown, or a budget that shrank under the row.
    """

    def _write() -> None:
        with _get_engine().begin() as connection:
            connection.execute(
                update(_neurocomment_campaign_channels).values(
                    paused_until=(datetime.now(UTC) - age).isoformat(),
                ),
            )

    await asyncio.to_thread(_write)


async def _end_a_round(monkeypatch: pytest.MonkeyPatch, post_id: int) -> None:
    """Re-arm the pair and hit the gate: one round ends and its window starts running.

    Readiness is restored first because the gate parks the pair; in production the next
    onboarding pass does that.
    """
    await upsert_readiness("acc-1", "@chan", joined=True, captcha_passed=True, ready=True)
    _patch_io(monkeypatch, comment=_CommentStub(status="failed", error_type=_GATE))
    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=post_id, text="hi"))


async def _gate_a_post(monkeypatch: pytest.MonkeyPatch, post_id: int) -> None:
    """Drive one full round: end it, then let its pause window elapse.

    Rewinding the deadline afterwards is what lets a test walk four rounds without waiting
    four days.
    """
    await _end_a_round(monkeypatch, post_id)
    await _rewind_the_pause()


async def _sweep_the_expired_windows() -> None:
    """The 5-minute sweep pass, which is what delivers a verdict deferred to a deadline."""
    await _channel_pause.review_expired_pauses(datetime.now(UTC))


async def _logged(event: str) -> bool:
    return any(entry.event == event for entry in await list_recent_logs(limit=100))


async def _counters(event: str) -> list[object]:
    """Every ``reason`` this event wrote, oldest first — the counters as the feed shows them.

    ``list_recent_logs`` answers newest first; the whole claim here is about a RUN of
    positions, so the order has to be the operator's.
    """
    return [
        entry.extra.get("reason")
        for entry in reversed(await list_recent_logs(limit=100))
        if entry.event == event
    ]


async def _add_untried_accounts(campaign_id: str, *accounts: str) -> None:
    """Assign accounts to the campaign but leave them with NO readiness row on ``@chan``.

    The onboarding backlog, reproduced: jitter and the rolling-24h join cap spread a fresh
    campaign's joins over days, and a paused channel turns the rest away outright — so
    these accounts have never been joined here, let alone gated.
    """
    for account_id in accounts:
        await create_account(
            AccountCreate(account_id=account_id, label=account_id, session_name=account_id)
        )
        await assign_account_to_campaign(campaign_id, account_id)


@pytest.mark.asyncio
async def test_k_failures_pause_the_channel_for_the_flat_window_and_bump_the_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _one_failure_per_round(monkeypatch)
    monkeypatch.setattr(settings.neurocomment, "channel_pause_hours", 24.0)
    campaign_id = await _make_campaign("@chan", "acc-1")
    _patch_io(monkeypatch, comment=_CommentStub(status="failed", error_type=_GATE))
    before = datetime.now(UTC)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=1, text="hi"))

    assert await _rounds(campaign_id) == 1
    until = await fetch_channel_paused_until("@chan")
    assert until is not None
    # A flat 24h, not the 1h first step of the doubling ladder this replaced. The
    # deadline is stamped a beat after ``before``, hence the minute of slack each way.
    window = datetime.fromisoformat(until) - before
    assert timedelta(hours=23, minutes=59) < window < timedelta(hours=24, minutes=1)


@pytest.mark.asyncio
async def test_a_pause_survives_a_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point of persisting it: in-memory state never reached round 4."""
    _one_failure_per_round(monkeypatch)
    campaign_id = await _make_campaign("@chan", "acc-1")
    _patch_io(monkeypatch, comment=_CommentStub(status="failed", error_type=_GATE))
    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=1, text="hi"))

    # The restart: every module dict the old back-off lived in is cleared.
    _state.reset_for_tests()

    assert await fetch_channel_paused_until("@chan") is not None
    assert await _rounds(campaign_id) == 1
    # ...and the state rebuilt from the DB still blocks the next post.
    comment = _CommentStub(status="ok")
    _patch_io(monkeypatch, comment=comment)
    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=2, text="hello world"))
    assert comment.calls == []


@pytest.mark.asyncio
async def test_a_delivered_comment_clears_both_the_window_and_the_rounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _one_failure_per_round(monkeypatch)
    campaign_id = await _make_campaign("@chan", "acc-1")
    await _gate_a_post(monkeypatch, post_id=1)
    assert await _rounds(campaign_id) == 1

    await upsert_readiness("acc-1", "@chan", joined=True, captcha_passed=True, ready=True)
    _patch_io(monkeypatch, comment=_CommentStub(status="ok"))
    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=2, text="hello world"))

    # The channel demonstrably works: its next bad day starts from round 0, unpaused.
    assert await _rounds(campaign_id) == 0
    assert await fetch_channel_paused_until("@chan") is None
    assert _state.register_write_failure("@chan", min_failures=2) is False


@pytest.mark.asyncio
async def test_the_final_round_pauses_and_its_window_is_sat_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """t≈24h: the last round pauses like every other one instead of dropping on the spot.

    The whole point of the 48h rule. Dropping as round 2 ended gave the channel ~24h,
    where the two sibling rules (join requests, re-join) both wait out the window their
    last attempt bought.
    """
    _one_failure_per_round(monkeypatch)
    assert settings.neurocomment.channel_max_rounds == 2
    campaign_id = await _make_campaign("@chan", "acc-1")

    await _gate_a_post(monkeypatch, post_id=1)  # round 1 pauses for 24h, then it elapses
    await _end_a_round(monkeypatch, post_id=2)  # round 2 is the last, and its day is due

    assert await _rounds(campaign_id) == 2
    assert await fetch_channel_paused_until("@chan") is not None
    assert await fetch_active_campaign_for_channel("@chan") is not None
    assert await _logged("neurocomment_channel_dropped") is False

    # ...and the sweep pass leaves the running window alone: the verdict is due at its end.
    await _sweep_the_expired_windows()

    assert await fetch_active_campaign_for_channel("@chan") is not None
    assert await _logged("neurocomment_channel_dropped") is False


@pytest.mark.asyncio
async def test_the_sweep_drops_the_channel_once_the_final_window_runs_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """t≈48h: the SHIPPED budget, not a pinned one — two rounds, two days, then it goes.

    Unpinned on purpose — the operator's rule is the default itself (one number for this
    rule and for ``_rejoin``), so a change to it must fail here rather than pass under a
    ``monkeypatch`` that keeps testing the retired four. The pass is what fires: the
    channel is paused, so no post reaches it and nothing on the post path could.
    """
    _one_failure_per_round(monkeypatch)
    assert settings.neurocomment.channel_max_rounds == 2

    await _make_campaign("@chan", "acc-1")
    await _gate_a_post(monkeypatch, post_id=1)  # round 1
    await _gate_a_post(monkeypatch, post_id=2)  # round 2, and its window elapses too

    await _sweep_the_expired_windows()

    # The active link is gone, so the listener reconciles and stops watching the channel.
    assert await fetch_active_campaign_for_channel("@chan") is None
    dropped = next(
        entry
        for entry in await list_recent_logs(limit=100)
        if entry.event == "neurocomment_channel_dropped"
    )
    assert dropped.extra["channel"] == "@chan"
    assert dropped.extra["rounds"] == 2
    assert dropped.extra["reason"] == "write_blocked"
    # No account leaves the chat: this is the channel forbidding comments, not a personal
    # ban, and re-joining later would spend the rolling-24h join cap for nothing.
    assert await _logged("neurocomment_account_banned") is False


@pytest.mark.asyncio
async def test_a_channel_outside_an_active_campaign_is_left_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rule its two sibling passes open with: only an active campaign loses a channel.

    A paused campaign posts nothing, so no window running out under it is evidence about
    the channel — the rounds it carries were all earned before the operator stopped it.
    """
    _one_failure_per_round(monkeypatch)
    campaign_id = await _make_campaign("@chan", "acc-1")
    await _gate_a_post(monkeypatch, post_id=1)
    await _gate_a_post(monkeypatch, post_id=2)  # the budget is spent and the window is out
    await set_campaign_status(campaign_id, "paused")

    await _sweep_the_expired_windows()

    assert (await list_campaign_channels(campaign_id)).links[0].active is True
    assert await _logged("neurocomment_channel_dropped") is False


@pytest.mark.asyncio
async def test_the_final_round_holds_while_a_serving_account_was_never_tried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Six serving accounts, three ever tried: the round budget must not unlink the channel.

    The coverage rule the three sibling drop rules already carry (``bans``, ``_sweep``,
    ``_rejoin``): a serving account with no readiness row was never tried here, not tried
    and failed. Here it bites hardest, because the pause is itself what keeps the other
    three out — ``_onboard_pair`` answers ``channel_paused`` and writes nothing, so they
    can never post the comment that would clear the rounds.
    """
    _one_failure_per_round(monkeypatch)
    monkeypatch.setattr(settings.neurocomment, "channel_max_rounds", 4)
    campaign_id = await _make_campaign("@chan", "acc-1", "acc-2", "acc-3")
    await _add_untried_accounts(campaign_id, "acc-4", "acc-5", "acc-6")

    for post_id in (1, 2, 3, 4, 5):  # one past the budget
        await _gate_a_post(monkeypatch, post_id=post_id)
    await _sweep_the_expired_windows()  # every one of those windows has run out

    assert await fetch_active_campaign_for_channel("@chan") is not None
    assert await _logged("neurocomment_channel_dropped") is False
    # The counter keeps climbing rather than freezing, so the verdict lands on the first
    # round that ends with the fleet complete — and the operator is told what is holding it.
    assert await _rounds(campaign_id) == 5
    assert any(
        entry.extra.get("untried_accounts") == 3
        for entry in await list_recent_logs(limit=100)
        if entry.event == "neurocomment_channel_paused"
    )
    # The held window is spent, not re-judged every five minutes: the deadline goes and the
    # rounds stay, so the next K failures buy the round that gets the verdict.
    assert await fetch_channel_paused_until("@chan") is None


@pytest.mark.asyncio
async def test_a_held_channel_drops_on_the_next_round_once_every_account_was_tried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The hold is a delay, not immortality: coverage completes, the next round unlinks."""
    _one_failure_per_round(monkeypatch)
    monkeypatch.setattr(settings.neurocomment, "channel_max_rounds", 4)
    campaign_id = await _make_campaign("@chan", "acc-1", "acc-2", "acc-3")
    await _add_untried_accounts(campaign_id, "acc-4", "acc-5", "acc-6")
    for post_id in (1, 2, 3, 4):
        await _gate_a_post(monkeypatch, post_id=post_id)
    assert await fetch_active_campaign_for_channel("@chan") is not None

    # A pause window elapsed, an onboarding pass finally reached the other three, and the
    # channel gated them too — the row ``_generate`` writes on a gate.
    for account_id in ("acc-4", "acc-5", "acc-6"):
        await upsert_readiness(account_id, "@chan", joined=True, captcha_passed=False, ready=False)
    await _gate_a_post(monkeypatch, post_id=5)
    await _sweep_the_expired_windows()

    assert await fetch_active_campaign_for_channel("@chan") is None
    dropped = next(
        entry
        for entry in await list_recent_logs(limit=100)
        if entry.event == "neurocomment_channel_dropped"
    )
    assert dropped.extra["rounds"] == 5


@pytest.mark.asyncio
async def test_an_account_pinned_to_another_channel_never_holds_this_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pin rule, from the same shared definition the three sibling rules resolve.

    A pinned account owes this channel nothing, so its missing row is not a missing try —
    treating it as one would make a channel with any pinned account undroppable.
    """
    _one_failure_per_round(monkeypatch)
    monkeypatch.setattr(settings.neurocomment, "channel_max_rounds", 4)
    campaign_id = await _make_campaign("@chan", "acc-1")
    await link_channel_to_campaign(campaign_id, "@other")
    await _add_untried_accounts(campaign_id, "acc-2")
    await set_campaign_account_channels(campaign_id, "acc-2", ["@other"])

    for post_id in (1, 2, 3, 4):
        await _gate_a_post(monkeypatch, post_id=post_id)
    await _sweep_the_expired_windows()

    assert await fetch_active_campaign_for_channel("@chan") is None


@pytest.mark.asyncio
async def test_a_paused_channel_blocks_posting(monkeypatch: pytest.MonkeyPatch) -> None:
    _one_failure_per_round(monkeypatch)
    await _make_campaign("@chan", "acc-1")
    _patch_io(monkeypatch, comment=_CommentStub(status="failed", error_type=_GATE))
    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=1, text="hi"))

    comment = _CommentStub(status="ok")
    _patch_io(monkeypatch, comment=comment)
    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=2, text="hello world"))

    assert comment.calls == []
    assert await fetch_comment("@chan", 2) is None


@pytest.mark.asyncio
async def test_an_expired_pause_lets_the_next_post_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing un-pauses a channel: the deadline simply passes and the next post tries."""
    _one_failure_per_round(monkeypatch)
    await _make_campaign("@chan", "acc-1")
    await _gate_a_post(monkeypatch, post_id=1)

    await upsert_readiness("acc-1", "@chan", joined=True, captcha_passed=True, ready=True)
    comment = _CommentStub(status="ok")
    _patch_io(monkeypatch, comment=comment)
    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=2, text="hello world"))

    assert len(comment.posts) == 1


# --------------------------------------------------------------------------- #
# What the verdict may NOT be read off: a stale snapshot, a stale window, or a
# channel the sibling re-join rule is still working on.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_comment_delivered_during_the_tick_saves_the_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pause is re-read at the verdict, because the bulk read is only a snapshot.

    The engine checks the pause ONCE, when a post arrives; generation then takes up to
    ~245s and the send follows, so a comment can be delivered minutes later — inside the
    same 5-minute tick that is judging the channel. ``clear_write_failures`` zeroes the
    rounds, but ``clear_channel_pause`` needs an ACTIVE link, so once the drop has happened
    the delivery can no longer undo it. Reproduced before the fix: comment ``posted``,
    channel unlinked, same tick.
    """
    _one_failure_per_round(monkeypatch)
    campaign_id = await _make_campaign("@chan", "acc-1")
    await _gate_a_post(monkeypatch, post_id=1)
    await _gate_a_post(monkeypatch, post_id=2)  # the budget is spent and the window is out
    snapshot = _channel_pause.fetch_active_campaigns_for_channels

    async def _deliver_then_read(channels: list[str]) -> object:
        """Land the comment after the sweep has taken its snapshot, before the verdict."""
        await upsert_readiness("acc-1", "@chan", joined=True, captcha_passed=True, ready=True)
        _patch_io(monkeypatch, comment=_CommentStub(status="ok"))
        await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=3, text="hello world"))
        return await snapshot(channels)

    monkeypatch.setattr(_channel_pause, "fetch_active_campaigns_for_channels", _deliver_then_read)

    await _sweep_the_expired_windows()

    delivered = await fetch_comment("@chan", 3)
    assert delivered is not None
    assert delivered.status == "posted"
    # The channel demonstrably works, so it keeps its link AND loses its rounds.
    assert await fetch_active_campaign_for_channel("@chan") is not None
    assert await _logged("neurocomment_channel_dropped") is False
    assert await _rounds(campaign_id) == 0


@pytest.mark.asyncio
async def test_a_resumed_campaign_is_not_sentenced_on_its_first_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A verdict comes off a FRESH window only — a long-expired deadline proves nothing.

    While the campaign is stopped nothing clears ``paused_until``, so the deadline simply
    sits there going stale. The sibling guard one line up already says a stopped campaign
    is not evidence and that the operator resuming it gives the fleet a new attempt; before
    this the code did the opposite, and the first tick after the resume unlinked the channel
    without a single post being tried. An app left down for a day leaves the same row.
    """
    _one_failure_per_round(monkeypatch)
    campaign_id = await _make_campaign("@chan", "acc-1")
    await _gate_a_post(monkeypatch, post_id=1)
    await _end_a_round(monkeypatch, post_id=2)  # the last round; its window starts running
    await set_campaign_status(campaign_id, "paused")
    await _rewind_the_pause(age=timedelta(days=7))  # the campaign sat stopped for a week
    await set_campaign_status(campaign_id, "active")

    await _sweep_the_expired_windows()

    assert await fetch_active_campaign_for_channel("@chan") is not None
    assert await _logged("neurocomment_channel_dropped") is False
    # Rounds kept, deadline released: the channel is judged on the next window it earns,
    # not on the one it slept through.
    assert await _rounds(campaign_id) == 2
    assert await fetch_channel_paused_until("@chan") is None


@pytest.mark.asyncio
async def test_the_verdict_waits_for_a_pair_still_inside_its_rejoin_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The concession ``_rejoin`` already makes this rule, made back the other way.

    ``_rejoin`` sits out a pause window rather than burn its budget on a channel nobody can
    even enter. Nothing paid that back: a re-join spent and logged as "1/2" could be
    annulled milliseconds later by this rule's deadline. The coverage check cannot catch it
    — a parked pair HAS a readiness row, so it counts as tried and ``untried`` reads zero.
    """
    _one_failure_per_round(monkeypatch)
    campaign_id = await _make_campaign("@chan", "acc-1", "acc-2")
    # acc-2 was kicked out of the discussion group: parked, tried here, budget untouched.
    await upsert_readiness("acc-2", "@chan", joined=False, captcha_passed=True, ready=False)
    await _gate_a_post(monkeypatch, post_id=1)
    await _gate_a_post(monkeypatch, post_id=2)  # the budget is spent and the window is out

    await _sweep_the_expired_windows()

    assert await fetch_active_campaign_for_channel("@chan") is not None
    assert await _logged("neurocomment_channel_dropped") is False
    # Held like an incomplete fleet is: the deadline goes, the rounds stay, and the channel
    # gets another round rather than this same window re-judged every five minutes.
    assert await _rounds(campaign_id) == 2
    assert await fetch_channel_paused_until("@chan") is None


@pytest.mark.asyncio
async def test_the_verdict_lands_once_the_rejoin_budget_is_spent_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The re-join hold is a delay, not immortality — the same shape as the coverage hold."""
    _one_failure_per_round(monkeypatch)
    await _make_campaign("@chan", "acc-1", "acc-2")
    await upsert_readiness("acc-2", "@chan", joined=False, captcha_passed=True, ready=False)
    for _ in range(settings.neurocomment.channel_max_rounds):
        await stamp_rejoin_attempt("acc-2", "@chan")
    await _gate_a_post(monkeypatch, post_id=1)
    await _gate_a_post(monkeypatch, post_id=2)

    await _sweep_the_expired_windows()

    assert await fetch_active_campaign_for_channel("@chan") is None
    dropped = next(
        entry
        for entry in await list_recent_logs(limit=100)
        if entry.event == "neurocomment_channel_dropped"
    )
    assert (dropped.extra["channel"], dropped.extra["rounds"]) == ("@chan", 2)


@pytest.mark.asyncio
async def test_every_pause_line_says_which_round_it_is_out_of_the_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The operator reads "1/2", then "2/2" — not two identical lines and a silent drop.

    ``rounds`` and ``max_rounds`` were already in ``extra``, which nobody but a developer
    reads; the feed showed the same sentence each round and the channel then vanished from
    the campaign with no sight of how close it had been. The last assertion is the one this
    rule needs and its siblings do not: the counter can outrun its budget here, because a
    held window is released and the channel earns another round, and "3/2" would read as
    arithmetic gone wrong rather than as a budget spent and a drop waiting on coverage.
    """
    _one_failure_per_round(monkeypatch)
    monkeypatch.setattr(settings.neurocomment, "channel_max_rounds", 2)
    campaign_id = await _make_campaign("@chan", "acc-1")
    await _add_untried_accounts(campaign_id, "acc-2")  # holds the drop, so round 3 exists

    for post_id in (1, 2, 3):
        await _gate_a_post(monkeypatch, post_id=post_id)
        await _sweep_the_expired_windows()

    assert await _rounds(campaign_id) == 3
    assert await _logged("neurocomment_channel_dropped") is False
    assert await _counters("neurocomment_channel_paused") == ["1/2", "2/2", "2/2"]
