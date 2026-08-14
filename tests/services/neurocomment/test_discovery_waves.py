"""Discovery stage 1 — how one run spends (and stops spending) its Telegram reads.

The merge, the cap and the persist decision live in ``test_discovery_search.py``
(700-line test cap); this file is only about the account's safety: which failures end a
run, and how the shared read budget is divided between the waves.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.telegram_client import TelegramReadError
from schemas.telegram_actions_discovery import GlobalPostsCursor
from services.neurocomment import _seams
from services.neurocomment._discovery_search import run_search
from services.neurocomment._state import in_cooldown
from tests.services.neurocomment.discovery_support import (
    LISTENER_ID,
    ReadRecorder,
    flood_error,
    matches,
    new_campaign,
    posts_page,
    read_error,
    search_request,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import BaseModel

    from schemas.neurocomment_discovery import DiscoverySearchStageResult, DiscoverySourceReport
    from schemas.telegram_actions import TelegramReadAction

pytestmark = pytest.mark.usefixtures("isolate_discovery")

# A post page that invites a second one: the cursor is there and the repeat page adds no
# new channel, which is the only end signal Telegram ever gives this search.
_PAGED_POSTS = posts_page(("posthit", "P", 50), cursor=GlobalPostsCursor(offset_rate=1, peer="p"))
# Enough distinct hits to fill the recommendation wave's seed list.
_RICH_SWEEP = matches(*((f"chan{index}", "C", 900 - index) for index in range(6)))


def _report_of(stage: DiscoverySearchStageResult, source: str) -> DiscoverySourceReport:
    reports = [report for report in stage.report.sources if report.source == source]
    assert len(reports) == 1
    return reports[0]


def _keywords(count: int) -> list[str]:
    return [f"word{index:02d}" for index in range(count)]


def _search_failing_on(fragment: str) -> Callable[[TelegramReadAction], BaseModel]:
    """A keyword search that refuses only the queries carrying ``fragment``."""

    def _search(action: TelegramReadAction) -> BaseModel:
        if fragment in getattr(action, "query", ""):
            reason = "RPC: TimeoutError"
            raise TelegramReadError(reason)
        return _RICH_SWEEP

    return _search


@pytest.mark.asyncio
async def test_a_premium_wait_stops_the_run_like_any_other_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FLOOD_PREMIUM_WAIT is the reply an account without Premium genuinely gets.

    It is no ``FloodWaitError`` subclass, so while the flood test was a regex over the
    gateway's ``FloodWait(<n>s)`` spelling it matched nothing: no cooldown was written,
    the run reported itself healthy, and every remaining read of the sweep plus the whole
    qualification pass fired into the live limit.
    """
    reader = ReadRecorder(search=flood_error(60, reason="FloodPremiumWait"))
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await new_campaign()

    stage = await run_search(campaign_id, LISTENER_ID, search_request(keywords=_keywords(4)))

    assert len(reader.calls) == 1
    assert stage.flooded is True
    assert in_cooldown(LISTENER_ID, datetime.now(UTC)) is True


@pytest.mark.asyncio
async def test_a_rate_limit_with_no_duration_takes_the_configured_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PEER_FLOOD carries no seconds; the comment engine's own default is the wait."""
    monkeypatch.setattr(settings.neurocomment, "peer_flood_cooldown_seconds", 1800)
    reader = ReadRecorder(search=read_error("PeerFlood", kind="flood_wait"))
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await new_campaign()

    stage = await run_search(campaign_id, LISTENER_ID, search_request(keywords=_keywords(4)))

    assert len(reader.calls) == 1
    assert stage.flooded is True
    assert in_cooldown(LISTENER_ID, datetime.now(UTC)) is True


@pytest.mark.asyncio
async def test_consecutive_failures_end_the_run_instead_of_draining_the_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wedged proxy answers nothing, so the rest of the run only proves it again.

    Without this the sweep moved to the next keyword forever and spent every read of the
    budget — each one up to three socket attempts down at the Telethon layer.
    """
    monkeypatch.setattr(settings.neurocomment, "discovery_max_consecutive_errors", 3)
    reader = ReadRecorder(search=read_error("RPC: TimeoutError"))
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await new_campaign()

    stage = await run_search(campaign_id, LISTENER_ID, search_request(keywords=_keywords(10)))

    assert len(reader.calls) == 3
    # Not a flood: no cooldown is written and the run keeps its own error, not a wait.
    assert stage.flooded is False
    assert in_cooldown(LISTENER_ID, datetime.now(UTC)) is False
    assert stage.error == "RPC: TimeoutError"


@pytest.mark.asyncio
async def test_the_failure_counter_spans_the_waves(monkeypatch: pytest.MonkeyPatch) -> None:
    """One broken session fails every wave alike, so the count is the run's, not a wave's."""
    monkeypatch.setattr(settings.neurocomment, "discovery_max_consecutive_errors", 3)
    reader = ReadRecorder(
        search=read_error("RPC: TimeoutError"),
        similar=read_error("RPC: TimeoutError"),
    )
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await new_campaign()

    await run_search(
        campaign_id,
        LISTENER_ID,
        search_request(keywords=_keywords(2), seed_channel="@durov"),
    )

    # Two keywords and the operator's seed — the third failure in a row — then nothing.
    assert len(reader.search_actions()) == 2
    assert len(reader.similar_actions()) == 1
    assert reader.posts_actions() == []


@pytest.mark.asyncio
async def test_an_answer_resets_the_failure_counter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scattered dead reads are an ordinary sweep; only a run of them is a dead session."""
    monkeypatch.setattr(settings.neurocomment, "discovery_max_consecutive_errors", 2)
    reader = ReadRecorder(search=_search_failing_on("1"))
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await new_campaign()

    # Every second keyword refuses, so two failures never land in a row.
    await run_search(campaign_id, LISTENER_ID, search_request(keywords=_keywords(6)))

    assert len(reader.search_actions()) == 6


@pytest.mark.asyncio
async def test_the_post_wave_yields_its_reads_to_the_recommendation_wave(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wave order alone let the weakest source spend the last of the budget on itself.

    The recommendation wave is the one source that reaches channels this account is
    nowhere near, and it runs last, so whatever the post pages had not eaten was all it
    ever got — nothing at all, on a long keyword list.
    """
    monkeypatch.setattr(settings.neurocomment, "discovery_max_reads_per_run", 10)
    reader = ReadRecorder(search=_RICH_SWEEP, posts=_PAGED_POSTS)
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await new_campaign()

    stage = await run_search(campaign_id, LISTENER_ID, search_request(keywords=_keywords(3)))

    # 3 sweep + 2 post pages + the 5 seeds held back for the wave = the whole budget.
    assert len(reader.search_actions()) == 3
    assert len(reader.posts_actions()) == 2
    assert len(reader.similar_actions()) == 5
    # The post wave is the one that says it stopped short, and it is not a run error.
    assert _report_of(stage, "telegram_posts").truncated is True
    assert _report_of(stage, "telegram_recommended").truncated is False
    assert stage.error is None


@pytest.mark.asyncio
async def test_a_full_keyword_list_runs_untruncated_at_default_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Truncation was the NORMAL case: 10 sweep + 1 seed + 13 post pages spent all 24.

    Both the post wave and the recommendation wave reported themselves out of budget on
    an ordinary run, the recommendation wave made no read at all, and the run still
    reported ``done`` and invited the operator to search again — up to 20 times a day.
    """
    reader = ReadRecorder(search=_RICH_SWEEP, posts=_PAGED_POSTS)
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await new_campaign()

    stage = await run_search(
        campaign_id,
        LISTENER_ID,
        search_request(keywords=_keywords(10), seed_channel="@durov"),
    )

    # 10 sweep + 1 seed + one post page per keyword + 5 recommendation seeds = 26, four
    # reads inside the shipped ceiling. The post wave caps its own total, so a long
    # keyword list buys one page each instead of two.
    assert len(reader.calls) == 26
    assert len(reader.posts_actions()) == 10
    assert len(reader.similar_actions()) == 6
    assert [report.truncated for report in stage.report.sources] == [False, False, False, False]


@pytest.mark.asyncio
async def test_the_recommendation_wave_never_re_reads_the_operators_seed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The seed pass already spent a getChannelRecommendations on it, seconds earlier."""
    reader = ReadRecorder(search=matches(("biggest", "B", 900), ("second", "S", 100)))
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await new_campaign()

    await run_search(
        campaign_id,
        LISTENER_ID,
        search_request(keywords=["crypto"], seed_channel="@biggest"),
    )

    # The operator's seed, then the sweep's next-best hit — never the same peer twice.
    assert [action.seed for action in reader.similar_actions()] == ["biggest", "second"]
