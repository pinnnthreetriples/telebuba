"""A channel that stops publishing leaves the campaign, and its authors leave its group.

The rule's whole difficulty is that OUR silence is not the channel's: the listener sees
posts only while the process is up, and this app restarts every day or two. So most of the
tests below are about the guard rather than the drop — only a DATED post older than the
cutoff retires a channel, and every other answer (a failed read, an empty one, an undated
message, a paused campaign) has to keep it. The drop deletes per-account pins nothing
restores, so absence of evidence must never reach it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.config import settings
from core.db import (
    list_campaign_channels,
    list_recent_logs,
    mark_pair_banned,
    set_listener_account_id,
    upsert_readiness,
)
from core.repositories.neurocomment import set_campaign_account_channels, set_campaign_status
from schemas.telegram_actions import NewPostEvent
from schemas.telegram_actions_activity import LastPostResult
from services.neurocomment import _inactive, _seams, engine
from tests.services.neurocomment.engine_support import (
    _CommentStub,
    _GenStub,
    _make_campaign,
    _patch_io,
)

pytestmark = pytest.mark.usefixtures("isolate_engine")

_CHANNEL = "@quiet"
_NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
# Far enough past the shipped 7 days that every link in these tests is a suspect on age
# alone; the cutoff arithmetic itself is asserted by the "not yet silent" case.
# Relative to the REAL clock: ``_make_campaign`` stamps links with ``datetime.now``, so a
# fixed instant here silently stops being "later" once the calendar catches up with it.
_LATER = datetime.now(UTC) + timedelta(days=30)
_LONG_DEAD = "2026-01-01T00:00:00+00:00"


@pytest.fixture(autouse=True)
def _forget_probes() -> None:
    """The probe clock is process-global, like every other in-memory rule state here."""
    _inactive.reset_probe_clock()


class _ProbeStub:
    """Answers the last-post read, and records which channels were actually probed."""

    def __init__(self, last_post_at: str | None) -> None:
        self.last_post_at = last_post_at
        self.channels: list[str] = []

    async def read(self, _account_id: str, action: object) -> LastPostResult:
        self.channels.append(getattr(action, "channel", ""))
        return LastPostResult(last_post_at=self.last_post_at)


class _FailingProbe:
    def __init__(self) -> None:
        self.channels: list[str] = []

    async def read(self, _account_id: str, action: object) -> LastPostResult:
        self.channels.append(getattr(action, "channel", ""))
        msg = "read failed"
        raise RuntimeError(msg)


async def _setup(*accounts: str) -> str:
    campaign_id = await _make_campaign(_CHANNEL, *accounts)
    await set_listener_account_id("listener")
    return campaign_id


async def _channel_is_linked(campaign_id: str) -> bool:
    links = (await list_campaign_channels(campaign_id)).links
    return any(link.channel == _CHANNEL and link.active for link in links)


async def _events() -> list[str]:
    return [row.event for row in await list_recent_logs(limit=50)]


def _leaves(comment: _CommentStub) -> list[str]:
    return [
        account_id
        for account_id, action in comment.calls
        if action.action_type == "leave_discussion_group"
    ]


def _left_channel(comment: _CommentStub) -> list[str]:
    return [
        account_id for account_id, action in comment.calls if action.action_type == "leave_channel"
    ]


@pytest.mark.asyncio
async def test_a_channel_telegram_confirms_is_dead_is_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one verdict: a dated post older than the cutoff. Authors hand back the group."""
    campaign_id = await _setup("acc-1", "acc-2")
    comment = _CommentStub()
    _patch_io(monkeypatch, comment=comment)
    probe = _ProbeStub(last_post_at=_LONG_DEAD)
    monkeypatch.setattr(_seams, "execute_read", probe.read)

    await _inactive.review_silent_channels(_LATER)

    assert probe.channels == [_CHANNEL]
    assert not await _channel_is_linked(campaign_id)
    assert sorted(_leaves(comment)) == ["acc-1", "acc-2"]
    dropped = next(
        row
        for row in await list_recent_logs(limit=50)
        if row.event == "neurocomment_channel_inactive_dropped"
    )
    # How long it was quiet, in the one ``extra`` field the log table renders beside the
    # label. A bare date would leave the operator doing the arithmetic that decided it.
    assert dropped.extra["reason"] == f"{(_LATER - datetime.fromisoformat(_LONG_DEAD)).days}d"
    assert dropped.extra["last_post_at"] == _LONG_DEAD


@pytest.mark.asyncio
async def test_the_listener_keeps_its_subscription(monkeypatch: pytest.MonkeyPatch) -> None:
    """The listener keeps its subscription, on purpose.

    Leaving honestly means stamping the standing join lost, which spends the listener's
    re-join budget — so a channel re-linked later could arrive already exhausted. One
    account holding a dead subscription is the cheaper mistake, and the unlink already
    stops the listener watching it.
    """
    await _setup("acc-1")
    comment = _CommentStub()
    _patch_io(monkeypatch, comment=comment)
    monkeypatch.setattr(_seams, "execute_read", _ProbeStub(last_post_at=_LONG_DEAD).read)

    await _inactive.review_silent_channels(_LATER)

    assert _left_channel(comment) == []


@pytest.mark.asyncio
async def test_an_empty_answer_is_unknown_and_never_a_drop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Telethon returns an empty list WITHOUT raising for a channel it can see but not read.

    It skips ``MessageEmpty`` silently and warns in its own source that some channels
    "return less messages than requested" for content excluded by local law. Reading that
    as "never published" would drop exactly the channels Telegram is being awkward about.
    """
    campaign_id = await _setup("acc-1")
    comment = _CommentStub()
    _patch_io(monkeypatch, comment=comment)
    monkeypatch.setattr(_seams, "execute_read", _ProbeStub(last_post_at=None).read)

    await _inactive.review_silent_channels(_LATER)

    assert await _channel_is_linked(campaign_id)
    assert _leaves(comment) == []
    assert "neurocomment_channel_activity_unknown" in await _events()


@pytest.mark.asyncio
async def test_a_channel_telegram_says_is_alive_is_kept_and_the_gap_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard that makes the rule safe: our silence alone convicts nobody.

    This is also the ONLY signal that names the listener's documented blind spot — a
    public channel that kicked us keeps resolving, so the loss arrives as silence.
    """
    campaign_id = await _setup("acc-1")
    comment = _CommentStub()
    _patch_io(monkeypatch, comment=comment)
    fresh = (_LATER - timedelta(hours=1)).isoformat()
    monkeypatch.setattr(_seams, "execute_read", _ProbeStub(last_post_at=fresh).read)

    await _inactive.review_silent_channels(_LATER)

    assert await _channel_is_linked(campaign_id)
    assert _leaves(comment) == []
    assert "neurocomment_channel_posts_missed" in await _events()
    # The stamp is repaired, so the next tick does not re-probe the same live channel.
    links = (await list_campaign_channels(campaign_id)).links
    assert next(link for link in links if link.channel == _CHANNEL).last_post_at == fresh


@pytest.mark.asyncio
async def test_a_probe_that_fails_decides_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A flood wait must not cost a live channel its accounts. The suspect simply keeps."""
    campaign_id = await _setup("acc-1")
    comment = _CommentStub()
    _patch_io(monkeypatch, comment=comment)
    monkeypatch.setattr(_seams, "execute_read", _FailingProbe().read)

    await _inactive.review_silent_channels(_LATER)

    assert await _channel_is_linked(campaign_id)
    assert _leaves(comment) == []
    assert "neurocomment_channel_activity_unknown" in await _events()


@pytest.mark.asyncio
async def test_a_failing_probe_is_not_repeated_every_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A suspect nothing can decide is not re-read every tick.

    A channel nothing can decide — deleted, private, an unresolvable handle — would
    otherwise cost 288 reads and 288 log rows a day for the life of the process.
    """
    await _setup("acc-1")
    _patch_io(monkeypatch, comment=_CommentStub())
    probe = _FailingProbe()
    monkeypatch.setattr(_seams, "execute_read", probe.read)

    await _inactive.review_silent_channels(_LATER)
    await _inactive.review_silent_channels(_LATER + timedelta(minutes=5))
    await _inactive.review_silent_channels(_LATER + timedelta(minutes=10))

    assert probe.channels == [_CHANNEL]

    # The hold expires; the channel is re-examined rather than abandoned.
    await _inactive.review_silent_channels(_LATER + timedelta(hours=2))
    assert probe.channels == [_CHANNEL, _CHANNEL]


@pytest.mark.asyncio
async def test_a_paused_campaign_is_never_judged(monkeypatch: pytest.MonkeyPatch) -> None:
    """A paused campaign is never judged.

    A paused campaign is unsubscribed, so NOTHING can stamp its channels and every one
    of them ages past any cutoff by construction. Judging them would dismantle the channel
    list of a campaign the operator deliberately stopped.
    """
    campaign_id = await _setup("acc-1")
    await set_campaign_status(campaign_id, "paused")
    probe = _ProbeStub(last_post_at=_LONG_DEAD)
    monkeypatch.setattr(_seams, "execute_read", probe.read)

    await _inactive.review_silent_channels(_LATER)

    assert probe.channels == []
    assert await _channel_is_linked(campaign_id)


@pytest.mark.asyncio
async def test_a_channel_seen_recently_is_never_probed(monkeypatch: pytest.MonkeyPatch) -> None:
    """No suspects, no RPCs: the common case must cost one query and nothing else."""
    await _setup("acc-1")
    _patch_io(monkeypatch, comment=_CommentStub())
    probe = _ProbeStub(last_post_at=None)
    monkeypatch.setattr(_seams, "execute_read", probe.read)

    # The link was created "now", so its own age is what the rule measures against.
    await _inactive.review_silent_channels(_NOW)

    assert probe.channels == []


@pytest.mark.asyncio
async def test_zero_days_disables_the_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    """The escape hatch: an operator who wants dead channels kept can keep them."""
    campaign_id = await _setup("acc-1")
    monkeypatch.setattr(settings.neurocomment, "inactive_channel_drop_days", 0.0)
    probe = _ProbeStub(last_post_at=_LONG_DEAD)
    monkeypatch.setattr(_seams, "execute_read", probe.read)

    await _inactive.review_silent_channels(_LATER)

    assert probe.channels == []
    assert await _channel_is_linked(campaign_id)


@pytest.mark.asyncio
async def test_a_pinned_account_is_still_walked_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """Who serves the channel must be read BEFORE the unlink.

    ``deactivate_channel`` deletes every per-account pin for the channel, and a pinned
    account then reads as not-serving — so read afterwards, it is left sitting in the group
    of a channel we just wrote off, silently and only for the pinned ones.
    """
    campaign_id = await _setup("acc-1", "acc-2")
    await set_campaign_account_channels(campaign_id, "acc-1", [_CHANNEL])
    comment = _CommentStub()
    _patch_io(monkeypatch, comment=comment)
    monkeypatch.setattr(_seams, "execute_read", _ProbeStub(last_post_at=_LONG_DEAD).read)

    await _inactive.review_silent_channels(_LATER)

    assert sorted(_leaves(comment)) == ["acc-1", "acc-2"]


@pytest.mark.asyncio
async def test_nobody_is_walked_out_of_a_chat_we_are_already_out_of(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rule ``_give_up`` was rewritten around: a leave for a chat we left is 2 wasted RPCs.

    A banned pair was marked and walked out by the ban ladder, and a pair that never
    joined was never in the group at all.
    """
    campaign_id = await _setup("acc-1", "acc-2")
    await mark_pair_banned("acc-1", _CHANNEL)
    await upsert_readiness("acc-2", _CHANNEL, joined=False, captcha_passed=False, ready=False)
    comment = _CommentStub()
    _patch_io(monkeypatch, comment=comment)
    monkeypatch.setattr(_seams, "execute_read", _ProbeStub(last_post_at=_LONG_DEAD).read)

    await _inactive.review_silent_channels(_LATER)

    assert not await _channel_is_linked(campaign_id)
    assert _leaves(comment) == []


@pytest.mark.asyncio
async def test_a_post_we_refuse_to_comment_on_still_proves_the_channel_alive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stamp lands before the filters, or the rule measures US instead of the channel.

    A channel that publishes nothing but forwards is skipped by the filters on every post
    — and would otherwise look silent and be dropped while publishing daily.
    """
    campaign_id = await _setup("acc-1")
    _patch_io(monkeypatch, comment=_CommentStub(), gen=_GenStub("hello"))

    await engine.handle_new_post(
        NewPostEvent(channel=_CHANNEL, post_id=1, text="x", is_forward=True),
    )

    links = (await list_campaign_channels(campaign_id)).links
    assert next(link for link in links if link.channel == _CHANNEL).last_post_at is not None
    assert "neurocomment_post_skipped" in await _events()


@pytest.mark.asyncio
async def test_the_stamp_never_moves_backwards(monkeypatch: pytest.MonkeyPatch) -> None:
    """The stamp never moves backwards.

    Two writers race: a live post stamping now, and the repair path carrying an older
    date read before it. Unguarded, the repair re-nominates a demonstrably active channel.
    """
    campaign_id = await _setup("acc-1")
    _patch_io(monkeypatch, comment=_CommentStub(), gen=_GenStub("hello"))
    await engine.handle_new_post(NewPostEvent(channel=_CHANNEL, post_id=1, text="hello there"))
    links = (await list_campaign_channels(campaign_id)).links
    fresh = next(link for link in links if link.channel == _CHANNEL).last_post_at

    from core.db import stamp_channel_post_seen  # noqa: PLC0415 - one call, one test

    await stamp_channel_post_seen(_CHANNEL, _LONG_DEAD)

    links = (await list_campaign_channels(campaign_id)).links
    assert next(link for link in links if link.channel == _CHANNEL).last_post_at == fresh
