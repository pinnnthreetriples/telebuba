"""A channel that stops publishing leaves the campaign, and everyone leaves the channel.

The rule's whole difficulty is that OUR silence is not the channel's: the listener sees
posts only while the process is up, and this app restarts every day or two. So the tests
below are mostly about the guard rather than the drop — a suspect is only ever convicted
on Telegram's own answer, and the two ways that answer can fail to arrive (a live channel,
a failed read) each keep the channel.
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
_LATER = _NOW + timedelta(days=30)


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
        self.calls = 0

    async def read(self, _account_id: str, _action: object) -> LastPostResult:
        self.calls += 1
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


def _leaves(comment: _CommentStub) -> list[tuple[str, str]]:
    return [
        (account_id, action.action_type)
        for account_id, action in comment.calls
        if action.action_type in {"leave_channel", "leave_discussion_group"}
    ]


@pytest.mark.asyncio
async def test_a_channel_telegram_confirms_is_dead_is_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The verdict: unlinked, and every membership it cost is handed back."""
    campaign_id = await _setup("acc-1", "acc-2")
    comment = _CommentStub()
    _patch_io(monkeypatch, comment=comment)
    probe = _ProbeStub(last_post_at="2026-01-01T00:00:00+00:00")
    monkeypatch.setattr(_seams, "execute_read", probe.read)

    await _inactive.review_silent_channels(_LATER)

    assert probe.channels == [_CHANNEL]
    assert not await _channel_is_linked(campaign_id)
    assert "neurocomment_channel_inactive_dropped" in await _events()
    # Both authors out of the discussion group, the listener out of the channel itself.
    assert sorted(_leaves(comment)) == [
        ("acc-1", "leave_discussion_group"),
        ("acc-2", "leave_discussion_group"),
        ("listener", "leave_channel"),
    ]


@pytest.mark.asyncio
async def test_a_channel_that_never_posted_at_all_is_dropped_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``None`` is an empty channel, not an unknown one — the link bought nothing."""
    campaign_id = await _setup("acc-1")
    _patch_io(monkeypatch, comment=_CommentStub())
    monkeypatch.setattr(_seams, "execute_read", _ProbeStub(last_post_at=None).read)

    await _inactive.review_silent_channels(_LATER)

    assert not await _channel_is_linked(campaign_id)
    dropped = next(
        row
        for row in await list_recent_logs(limit=50)
        if row.event == "neurocomment_channel_inactive_dropped"
    )
    # Spelt out rather than left blank: "never published" and "went quiet" are different
    # mistakes, and the feed is where the operator tells them apart.
    assert dropped.extra["last_post_at"] == "never"


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
async def test_a_channel_seen_recently_is_never_probed(monkeypatch: pytest.MonkeyPatch) -> None:
    """No suspects, no RPCs: the common case must cost one indexed query and nothing else."""
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
    probe = _ProbeStub(last_post_at=None)
    monkeypatch.setattr(_seams, "execute_read", probe.read)

    await _inactive.review_silent_channels(_LATER)

    assert probe.channels == []
    assert await _channel_is_linked(campaign_id)


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
    monkeypatch.setattr(_seams, "execute_read", _ProbeStub(last_post_at=None).read)

    await _inactive.review_silent_channels(_LATER)

    assert not await _channel_is_linked(campaign_id)
    assert _leaves(comment) == [("listener", "leave_channel")]


@pytest.mark.asyncio
async def test_a_post_we_refuse_to_comment_on_still_proves_the_channel_alive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stamp lands before every gate, or the rule measures US instead of the channel.

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
    events = await _events()
    assert "neurocomment_post_skipped" in events
