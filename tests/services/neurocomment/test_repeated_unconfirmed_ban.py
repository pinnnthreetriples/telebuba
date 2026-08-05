"""The repeated-unconfirmed-ban rule (#47): two refusals, a day apart, inside 48h.

``UserBannedInChannelError`` is account-wide evidence, so the per-group ladder
(``bans.confirm_group_ban_and_leave``) rightly refuses to park a pair on one. What it left
behind was a pair that could be refused forever: the cooldown expires, selection hands the
same account the same channel's next post, and the same error comes back — live DB, one
account against one channel, four times running, ten failures and zero comments over three
days. These tests pin the budget that ends it, and the four things it must NOT do: count
what is not this error, count a refusal the group is not to blame for, spend the whole
budget in one hour, or outlive its window / survive a delivered comment.

The cooldown is deliberately NOT zeroed here. It is the rule's clock — a counted refusal
parks the pair for ``channel_pause_hours`` and nothing counts again until that expires —
so a fixture that flattened it would let these tests observe a tempo production never has.
Where a test needs the next day, it says so through ``_the_pause_expires``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import fetch_readiness, list_recent_logs
from schemas.telegram_actions import BanCheckResult, NewPostEvent
from services.neurocomment import _seams, _state, bans, engine
from tests.services.neurocomment.engine_support import (
    _CommentStub,
    _GenStub,
    _make_campaign,
    _patch_ban_confirmation,
    _patch_io,
)

if TYPE_CHECKING:
    from schemas.spam_status import SpamStatusKind

_CHANNEL = "@chan"
_ACCOUNT = "acc-1"
_BAN_ERROR = "UserBannedInChannelError"


@pytest.fixture
def _budget_of_two(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the rule's two inputs, so the operator retuning them cannot rewrite the tests.

    ``channel_max_rounds`` is the threshold and, with ``channel_pause_hours``, both the
    window (2 x 24h = the 48h every sibling rule counts out) and the minimum interval
    between two counted refusals (24h).
    """
    monkeypatch.setattr(settings.neurocomment, "channel_max_rounds", 2)
    monkeypatch.setattr(settings.neurocomment, "channel_pause_hours", 24.0)


class _Posts:
    """Drives consecutive posts on one channel, each with its own outcome and text.

    Distinct texts per post because the semantic dedup is on by default: a second comment
    identical to a delivered one is rejected before it ever reaches the ban branch.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.monkeypatch = monkeypatch
        self.gen = _GenStub("alpha text", "beta text", "gamma text", "delta text")
        self.post_id = 0
        self.stub = _CommentStub()

    async def send(self, *, error_type: str | None = None) -> None:
        self.post_id += 1
        self.stub = _CommentStub(
            status="ok" if error_type is None else "failed",
            error_type=error_type,
        )
        _patch_io(self.monkeypatch, comment=self.stub, gen=self.gen)
        await engine.handle_new_post(
            NewPostEvent(channel=_CHANNEL, post_id=self.post_id, text="hi"),
        )

    @property
    def left_the_chat(self) -> bool:
        return any(action.action_type == "leave_discussion_group" for _, action in self.stub.calls)


def _the_pause_expires() -> None:
    """The next day: every live cooldown deadline is behind us.

    ``_state`` reads deadlines from memory (the DB copy only survives restarts), so
    dropping them is exactly what the clock reaching them looks like to every caller —
    the engine's selection gate and the rule's own minimum-interval check alike.
    """
    _state.reset_for_tests()


def _spy_on_the_counter(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record the window start of every stamp the rule actually spends, and count them.

    Delegates to the real repository, so what is asserted is the rule's behaviour and not
    the stub's: a refusal that must not be counted leaves this list empty.
    """
    real = bans.stamp_unconfirmed_ban
    windows: list[str] = []

    async def _stamp(account_id: str, channel: str, window_start: str) -> int:
        windows.append(window_start)
        return await real(account_id, channel, window_start)

    monkeypatch.setattr(bans, "stamp_unconfirmed_ban", _stamp)
    return windows


async def _banned(account_id: str = _ACCOUNT, channel: str = _CHANNEL) -> bool:
    readiness = await fetch_readiness(account_id, channel)
    assert readiness is not None
    return readiness.banned


async def _times_logged(code: str) -> int:
    return sum(1 for entry in await list_recent_logs(limit=200) if entry.event == code)


@pytest.mark.asyncio
@pytest.mark.usefixtures("isolate_engine", "_budget_of_two")
async def test_the_first_unconfirmed_refusal_leaves_the_pair_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unchanged from before the rule: one refusal proves nothing, so it only cools."""
    await _make_campaign(_CHANNEL, _ACCOUNT)
    _patch_ban_confirmation(monkeypatch, state="can_send")
    posts = _Posts(monkeypatch)

    await posts.send(error_type=_BAN_ERROR)

    assert await _banned() is False
    assert posts.left_the_chat is False
    assert await _times_logged("neurocomment_post_ban_unconfirmed") == 1


@pytest.mark.asyncio
@pytest.mark.usefixtures("isolate_engine", "_budget_of_two")
async def test_a_second_refusal_the_same_day_is_not_counted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The budget is two refusals in 48h, not two in an hour — so the second waits a day.

    Driven directly: through the engine the pause also stops the pair being SELECTED, so
    this is the only level at which the counter's own guard is visible.
    """
    await _make_campaign(_CHANNEL, _ACCOUNT)
    _patch_ban_confirmation(monkeypatch, state="can_send")
    windows = _spy_on_the_counter(monkeypatch)

    first = await bans.register_unconfirmed_ban(_ACCOUNT, _CHANNEL, known_state="can_send")
    second = await bans.register_unconfirmed_ban(_ACCOUNT, _CHANNEL, known_state="can_send")

    # And the log says so: the counted refusal reports its position, the one inside the
    # cooldown reports nothing at all rather than a second "1/2".
    assert (first, second) == ("1/2", None)
    assert len(windows) == 1
    assert await _banned() is False


@pytest.mark.asyncio
@pytest.mark.usefixtures("isolate_engine", "_budget_of_two")
async def test_the_second_refusal_a_pause_later_parks_the_pair_and_leaves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point: the pair stops coming back to a chat that will not take it.

    And says so ONCE — the exit logs the ban itself, so the post outcome must not repeat
    the same code underneath it.
    """
    await _make_campaign(_CHANNEL, _ACCOUNT)
    _patch_ban_confirmation(monkeypatch, state="can_send")
    posts = _Posts(monkeypatch)

    await posts.send(error_type=_BAN_ERROR)
    _the_pause_expires()
    await posts.send(error_type=_BAN_ERROR)

    assert await _banned() is True
    assert posts.left_the_chat is True
    assert await _times_logged("neurocomment_account_banned") == 1
    assert await _times_logged("neurocomment_group_ban_confirmed") == 0


@pytest.mark.asyncio
@pytest.mark.usefixtures("isolate_engine", "_budget_of_two")
@pytest.mark.parametrize(
    ("known_state", "spam"),
    [
        # The account is limited: the write block is account-wide, and the same error is
        # landing in every other channel it posts to. Counting it would take the whole
        # fleet's channels away one at a time.
        ("restricted", "limited"),
        ("can_send", "limited"),
        # The probe never landed, so nothing at all is known about this group.
        ("probe_error", "clean"),
        # Nothing to leave, and it collapses kicked / never-joined / left.
        ("not_member", "clean"),
    ],
)
async def test_only_a_group_refusing_a_healthy_account_spends_the_budget(
    monkeypatch: pytest.MonkeyPatch,
    known_state: str,
    spam: SpamStatusKind,
) -> None:
    """Anything but ``can_send`` + a clean @SpamBot points away from THIS group."""
    await _make_campaign(_CHANNEL, _ACCOUNT)
    _patch_ban_confirmation(monkeypatch, state="can_send", spam=spam)
    windows = _spy_on_the_counter(monkeypatch)

    assert await bans.register_unconfirmed_ban(_ACCOUNT, _CHANNEL, known_state=known_state) is None

    assert windows == []
    assert await _banned() is False


@pytest.mark.asyncio
@pytest.mark.usefixtures("isolate_engine")
async def test_a_single_round_budget_still_takes_two_refusals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``channel_max_rounds=1`` is legal, and must not mean "sticky ban on failure one".

    One setting plays three roles; in this one it is a floor of two, because no single
    unproven refusal may close a channel for a pair for good.
    """
    monkeypatch.setattr(settings.neurocomment, "channel_max_rounds", 1)
    monkeypatch.setattr(settings.neurocomment, "channel_pause_hours", 24.0)
    await _make_campaign(_CHANNEL, _ACCOUNT)
    _patch_ban_confirmation(monkeypatch, state="can_send")
    _patch_io(monkeypatch, comment=_CommentStub())  # the ban's leave rides this seam

    # "1/2" and "2/2" even here: the position is read off the floor the rule actually
    # runs on, never off the raw setting, so the operator is never told a refusal is the
    # first of one.
    first = await bans.register_unconfirmed_ban(_ACCOUNT, _CHANNEL, known_state="can_send")
    assert first == "1/2"
    assert await _banned() is False

    _the_pause_expires()

    assert await bans.register_unconfirmed_ban(_ACCOUNT, _CHANNEL, known_state="can_send") == "2/2"
    assert await _banned() is True


@pytest.mark.asyncio
@pytest.mark.usefixtures("isolate_engine", "_budget_of_two")
async def test_a_delivered_comment_between_refusals_puts_the_budget_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A comment that landed is proof the pair can write here — the count means nothing now."""
    await _make_campaign(_CHANNEL, _ACCOUNT)
    _patch_ban_confirmation(monkeypatch, state="can_send")
    posts = _Posts(monkeypatch)

    await posts.send(error_type=_BAN_ERROR)
    _the_pause_expires()
    await posts.send()
    _the_pause_expires()
    await posts.send(error_type=_BAN_ERROR)

    assert await _times_logged("neurocomment_posted") == 1
    assert await _banned() is False
    assert posts.left_the_chat is False


@pytest.mark.asyncio
@pytest.mark.usefixtures("isolate_engine", "_budget_of_two")
async def test_a_confirmed_ban_still_lands_on_the_very_first_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The budget is for the UNPROVEN case; proof is still worth exactly one failure.

    Two lines, two codes: the confirmation and the post outcome. That is the pair the
    budget exit must not turn into the same code twice.
    """
    await _make_campaign(_CHANNEL, _ACCOUNT)
    _patch_ban_confirmation(monkeypatch)  # restricted participant record + clean @SpamBot
    posts = _Posts(monkeypatch)

    await posts.send(error_type=_BAN_ERROR)

    assert await _banned() is True
    assert posts.left_the_chat is True
    assert await _times_logged("neurocomment_group_ban_confirmed") == 1
    assert await _times_logged("neurocomment_account_banned") == 1


@pytest.mark.asyncio
@pytest.mark.usefixtures("isolate_engine", "_budget_of_two")
async def test_one_refused_post_pays_for_one_participant_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both verdicts read the same record, so the post path asks Telegram once."""
    await _make_campaign(_CHANNEL, _ACCOUNT)
    _patch_ban_confirmation(monkeypatch, state="can_send")
    probes = 0

    async def _read(_account_id: str, _action: object) -> BanCheckResult:
        nonlocal probes
        probes += 1
        return BanCheckResult(state="can_send")

    monkeypatch.setattr(_seams, "execute_read", _read)

    await _Posts(monkeypatch).send(error_type=_BAN_ERROR)

    assert probes == 1


@pytest.mark.asyncio
@pytest.mark.usefixtures("isolate_engine", "_budget_of_two")
@pytest.mark.parametrize("error_type", ["ChatWriteForbiddenError", "ChannelPrivateError"])
async def test_the_other_write_failures_are_not_counted_towards_this_ban(
    monkeypatch: pytest.MonkeyPatch,
    error_type: str,
) -> None:
    """An admin mute and a kick mean something else and have their own branches.

    Asserted on the counter itself, not on the outcome: both branches park the pair
    (``ready=False``) for reasons of their own, so "not banned" would pass even if the
    refusal had been counted.
    """
    await _make_campaign(_CHANNEL, _ACCOUNT)
    _patch_ban_confirmation(monkeypatch, state="can_send")
    windows = _spy_on_the_counter(monkeypatch)

    await _Posts(monkeypatch).send(error_type=error_type)

    assert windows == []
    assert await _banned() is False


@pytest.mark.asyncio
@pytest.mark.usefixtures("isolate_engine", "_budget_of_two")
async def test_the_window_is_the_pause_hours_times_the_round_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """48h, derived from the two settings the sibling rules already use — not a new knob."""
    await _make_campaign(_CHANNEL, _ACCOUNT)
    _patch_ban_confirmation(monkeypatch, state="can_send")
    windows = _spy_on_the_counter(monkeypatch)
    before = datetime.now(UTC)

    assert await bans.register_unconfirmed_ban(_ACCOUNT, _CHANNEL, known_state="can_send") == "1/2"

    window = before - datetime.fromisoformat(windows[0])
    assert timedelta(hours=47, minutes=59) < window <= timedelta(hours=48)


async def _counters(code: str) -> list[object]:
    """Every ``reason`` this event wrote, oldest first — the counters as the feed shows them.

    ``list_recent_logs`` answers newest first, and the claim here is about a RUN of
    positions, so the order has to be the operator's.
    """
    return [
        entry.extra.get("reason")
        for entry in reversed(await list_recent_logs(limit=200))
        if entry.event == code
    ]


@pytest.mark.asyncio
@pytest.mark.usefixtures("isolate_engine", "_budget_of_two")
async def test_the_feed_counts_out_the_refusals_the_budget_was_charged_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rule an operator could not follow: nothing said how close the pair was.

    Two refusals over two days and a ban, with the only visible difference between the
    first line and the second being their timestamps — then a third code appearing out of
    nowhere. The ban line closes the run at ``2/2`` rather than starting a count of its own.
    """
    await _make_campaign(_CHANNEL, _ACCOUNT)
    _patch_ban_confirmation(monkeypatch, state="can_send")
    posts = _Posts(monkeypatch)

    await posts.send(error_type=_BAN_ERROR)
    _the_pause_expires()
    await posts.send(error_type=_BAN_ERROR)

    assert await _counters("neurocomment_post_ban_unconfirmed") == ["1/2", "2/2"]
    assert await _counters("neurocomment_account_banned") == ["2/2"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("isolate_engine", "_budget_of_two")
async def test_a_refusal_the_budget_never_paid_for_carries_no_counter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same event code, no count spent — so a counter beside it would be a lie.

    A globally limited account is refused everywhere, and this rule deliberately charges
    it to nobody. The line keeps the shape it had before the counters existed: its
    Telegram status, which is what ``eventReason`` falls back to with no ``reason``.
    """
    await _make_campaign(_CHANNEL, _ACCOUNT)
    _patch_ban_confirmation(monkeypatch, state="can_send", spam="limited")

    await _Posts(monkeypatch).send(error_type=_BAN_ERROR)

    assert await _counters("neurocomment_post_ban_unconfirmed") == [None]
    assert await _banned() is False
