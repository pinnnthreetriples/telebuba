"""Discovery run lifecycle — single-flight, the search allowance, and the phase machine.

``_run`` is what the whole UI reads through ``DiscoveryBoard.progress``, and the
start path claims a slot before it awaits. Both are covered here rather than in the
stage files, because both are about the run as a whole.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.repositories.neurocomment import (
    list_discovery_candidates,
    replace_discovery_candidates,
)
from core.telegram_client import TelegramReadError
from schemas.neurocomment_discovery import (
    DiscoveryCandidateRow,
    DiscoveryChannelVerdict,
    DiscoveryRunReport,
    DiscoverySearchStageResult,
    DiscoverySourceReport,
)
from services.neurocomment import _discovery_state, _seams
from services.neurocomment import discovery as discovery_module
from services.neurocomment.discovery import start_discovery
from tests.services.neurocomment.discovery_support import (
    ReadRecorder,
    drain_discovery,
    matches,
    new_campaign,
    read_error,
    search_request,
    seed_listener,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import BaseModel

    from schemas.telegram_actions import TelegramReadAction

pytestmark = pytest.mark.usefixtures("isolate_discovery")

_CONCURRENT_STARTS = 2
_FLOOD_SECONDS = 900
_FLOOD_REASON = f"FloodWait({_FLOOD_SECONDS}s)"


async def _seed_candidates(campaign_id: str, *channels: str) -> None:
    await replace_discovery_candidates(
        campaign_id,
        [
            DiscoveryCandidateRow(channel=channel, title=channel.title(), source="telegram_search")
            for channel in channels
        ],
    )


async def _channels_of(campaign_id: str) -> list[str]:
    return [row.channel for row in (await list_discovery_candidates(campaign_id)).rows]


def _hit_then_flood() -> Callable[[TelegramReadAction], BaseModel]:
    """A keyword search that answers once, then floods — a partly-successful sweep."""
    answered: list[str] = []

    def _search(_action: TelegramReadAction) -> BaseModel:
        if answered:
            raise TelegramReadError(_FLOOD_REASON, kind="flood_wait", seconds=_FLOOD_SECONDS)
        answered.append("done")
        return matches(("found", "F", None))

    return _search


@pytest.mark.asyncio
async def test_two_concurrent_starts_produce_exactly_one_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolving the account awaits, so the slot must be claimed before that.

    Otherwise both starts pass the is-running check, the loser overwrites the winner
    in the task table and becomes untrackable: two paced RPC streams on one account,
    racing over the same candidate rows, and unreachable by shutdown.
    """
    reader = ReadRecorder(search=matches(("alpha", "Alpha", None)))
    hold = asyncio.Event()

    async def _held_read(account_id: str, action: TelegramReadAction) -> BaseModel:
        # Pin the spawned run open until both starts have answered. Gating account
        # resolution alone is not enough: ``start_discovery`` awaits ``log_event``
        # right after ``spawn``, and during that yield the whole run can finish and
        # drop out of ``is_running``. The second start then claims the slot for real
        # — two "started" replies that are CORRECT, because by then there was nothing
        # left to be concurrent with. Holding the run's Telegram reads keeps it
        # un-finishable, so the window under test genuinely stays open.
        await hold.wait()
        return await reader(account_id, action)

    monkeypatch.setattr(_seams, "execute_read", _held_read)
    await seed_listener()
    campaign_id = await new_campaign()

    gate = asyncio.Event()
    both_arrived = asyncio.Event()
    entered: list[str] = []
    resolve = discovery_module.resolve_search_account

    async def _gated(target: str) -> object:
        # Park BOTH starts inside the window on purpose. Left to the event loop, the
        # two coroutines happen to serialize and the test passes even with no claim at
        # all — which is exactly how the first version of this test proved nothing.
        entered.append(target)
        if len(entered) == _CONCURRENT_STARTS:
            both_arrived.set()
        await gate.wait()
        return await resolve(target)

    monkeypatch.setattr(discovery_module, "resolve_search_account", _gated)
    both = asyncio.gather(
        start_discovery(campaign_id, search_request()),
        start_discovery(campaign_id, search_request()),
    )
    await both_arrived.wait()
    gate.set()
    first, second = await both
    # Only now, with both verdicts in hand, may the run proceed and be awaited — a
    # release before this point would reopen the very race the hold exists to close.
    hold.set()
    await drain_discovery(campaign_id)

    assert first is not None
    assert second is not None
    assert {first.status, second.status} == {"started", "already_running"}
    # One keyword, one run: a second stream would show up as a second search RPC.
    assert len(reader.search_actions()) == 1


@pytest.mark.asyncio
async def test_a_second_campaign_cannot_open_a_parallel_stream_on_one_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-campaign single-flight alone does not deliver the one-paced-stream rule.

    Every campaign resolves to the same fleet listener, so N campaigns would otherwise
    mean N simultaneous RPC streams on one account — the burst the pacing exists to
    avoid, and the allowance bounds total searches, not concurrency.
    """
    running = asyncio.Event()

    async def _hang(*_args: object, **_kwargs: object) -> DiscoverySearchStageResult:
        running.set()
        await asyncio.Event().wait()
        return DiscoverySearchStageResult()

    monkeypatch.setattr(discovery_module, "run_search", _hang)
    await seed_listener()
    first_campaign = await new_campaign()
    second_campaign = await new_campaign()

    first = await start_discovery(first_campaign, search_request())
    await running.wait()
    second = await start_discovery(second_campaign, search_request())

    assert first is not None
    assert second is not None
    assert first.status == "started"
    assert second.status == "already_running"


@pytest.mark.asyncio
async def test_a_refused_start_spends_nothing() -> None:
    """The claim comes after account resolution, so a refusal never has one to give back."""
    campaign_id = await new_campaign()

    refused = await start_discovery(campaign_id, search_request())

    assert refused is not None
    assert refused.status == "no_account"
    assert _discovery_state.is_running(campaign_id) is False
    assert _discovery_state.at_daily_search_cap() is False


@pytest.mark.asyncio
async def test_an_unknown_campaign_is_refused_before_anything_is_spent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Account resolution falls back to the global listener, so it cannot catch this."""
    reader = ReadRecorder(search=matches(("alpha", "Alpha", None)))
    monkeypatch.setattr(_seams, "execute_read", reader)
    await seed_listener()

    assert await start_discovery("ghost-campaign", search_request()) is None
    assert reader.calls == []
    assert _discovery_state.at_daily_search_cap() is False


@pytest.mark.asyncio
async def test_a_clean_run_ends_in_done(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches(("alpha", "A", 500))))
    await seed_listener()
    campaign_id = await new_campaign()

    await start_discovery(campaign_id, search_request())
    await drain_discovery(campaign_id)

    assert _discovery_state.phase_of(campaign_id) == "done"
    assert _discovery_state.last_error(campaign_id) is None
    assert _discovery_state.is_running(campaign_id) is False


@pytest.mark.asyncio
async def test_a_totally_failed_search_keeps_the_previous_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The replace is delete-then-insert, so an empty write would destroy the set."""
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=read_error("RPC: Timeout")))
    await seed_listener()
    campaign_id = await new_campaign()
    await _seed_candidates(campaign_id, "kept_one", "kept_two")

    await start_discovery(campaign_id, search_request())
    await drain_discovery(campaign_id)

    assert await _channels_of(campaign_id) == ["kept_one", "kept_two"]
    # And the operator is told, rather than reading a successful-looking empty run.
    assert _discovery_state.phase_of(campaign_id) == "failed"
    assert _discovery_state.last_error(campaign_id) == "RPC: Timeout"


@pytest.mark.asyncio
async def test_a_search_that_genuinely_found_nothing_still_replaces_the_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The counterpart to the case above: no error means the empty result is the truth."""
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches()))
    await seed_listener()
    campaign_id = await new_campaign()
    await _seed_candidates(campaign_id, "stale_one")

    await start_discovery(campaign_id, search_request())
    await drain_discovery(campaign_id)

    assert await _channels_of(campaign_id) == []
    assert _discovery_state.phase_of(campaign_id) == "done"


@pytest.mark.asyncio
async def test_a_partial_search_failure_still_qualifies_what_it_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A degraded source is a reason on a finished run, not a failed one."""
    monkeypatch.setattr(
        _seams,
        "execute_read",
        ReadRecorder(search=matches(("alpha", "A", None)), similar=read_error("RPC: Timeout")),
    )
    await seed_listener()
    campaign_id = await new_campaign()

    await start_discovery(campaign_id, search_request(seed_channel="@durov"))
    await drain_discovery(campaign_id)

    assert await _channels_of(campaign_id) == ["alpha"]
    assert _discovery_state.phase_of(campaign_id) == "done"
    assert _discovery_state.last_error(campaign_id) == "RPC: Timeout"


@pytest.mark.asyncio
async def test_a_degraded_source_with_no_hits_still_replaces_the_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nobody-answered is the discriminator, not no-rows-plus-a-reason.

    The keyword search answering honestly with zero hits while the seed pass fails is an
    empty *result*: serving the previous run's rows here would present channels from a
    different keyword set as this run's findings, ticked and adoptable.
    """
    monkeypatch.setattr(
        _seams,
        "execute_read",
        ReadRecorder(search=matches(), similar=read_error("RPC: Timeout")),
    )
    await seed_listener()
    campaign_id = await new_campaign()
    await _seed_candidates(campaign_id, "from_last_run")

    await start_discovery(campaign_id, search_request(seed_channel="@durov"))
    await drain_discovery(campaign_id)

    assert await _channels_of(campaign_id) == []
    assert _discovery_state.phase_of(campaign_id) == "done"
    # The degraded source is still reported, just not as a failed run.
    assert _discovery_state.last_error(campaign_id) == "RPC: Timeout"


@pytest.mark.asyncio
async def test_a_flood_wait_does_not_enter_qualification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The search stage just wrote this account's cooldown; nothing else re-checks it.

    ``_seams.execute_read`` is the raw gateway with no gate, so qualifying would fire
    getFullChannel straight into the live window — which is how Telegram escalates.
    """
    reader = ReadRecorder(search=_hit_then_flood())
    monkeypatch.setattr(_seams, "execute_read", reader)
    await seed_listener()
    campaign_id = await new_campaign()

    await start_discovery(campaign_id, search_request(keywords=["alpha", "bravo"]))
    await drain_discovery(campaign_id)

    assert reader.actions_of("get_linked_discussion_group") == []
    assert _discovery_state.phase_of(campaign_id) == "failed"
    assert _discovery_state.last_error(campaign_id) == _FLOOD_REASON
    # The first keyword's hit is NOT stored: see the test below for why a run this run
    # never finished must not become the campaign's candidate set.
    assert await _channels_of(campaign_id) == []


@pytest.mark.asyncio
async def test_a_flood_mid_sweep_keeps_the_previous_candidate_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run cut short trades nothing away: the reviewed set outranks a fragment.

    Storing what the flooded run happened to reach deleted an already-qualified set,
    put a handful of unqualified handles in its place, reported the run failed (a flood
    skips qualification) and left the account on cooldown — so the operator could not
    even search again to get their candidates back.
    """
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=_hit_then_flood()))
    await seed_listener()
    campaign_id = await new_campaign()
    await _seed_candidates(campaign_id, "qualified_one", "qualified_two")

    await start_discovery(campaign_id, search_request(keywords=["alpha", "bravo"]))
    await drain_discovery(campaign_id)

    assert await _channels_of(campaign_id) == ["qualified_one", "qualified_two"]
    assert _discovery_state.phase_of(campaign_id) == "failed"


@pytest.mark.asyncio
async def test_a_new_run_does_not_serve_the_previous_run_s_verdicts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verdicts are per-run, and a cached linked group makes this run spend no probe.

    So a verdict left over from three runs ago would be shown against a row nothing
    measured this time — and the map would grow for the life of the process.
    """
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches(("alpha", "A", 500))))
    await seed_listener()
    campaign_id = await new_campaign()
    _discovery_state.record_verdict(campaign_id, "stale", DiscoveryChannelVerdict(scam=True))

    await start_discovery(campaign_id, search_request())
    await drain_discovery(campaign_id)

    assert "stale" not in _discovery_state.verdicts(campaign_id)


@pytest.mark.asyncio
async def test_a_filter_that_removed_every_hit_replaces_the_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The source answered; the operator's own filter is what emptied the result."""
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches(("small", "S", 42))))
    await seed_listener()
    campaign_id = await new_campaign()
    await _seed_candidates(campaign_id, "from_last_run")

    await start_discovery(campaign_id, search_request(members_min=10_000))
    await drain_discovery(campaign_id)

    assert await _channels_of(campaign_id) == []
    assert _discovery_state.phase_of(campaign_id) == "done"


@pytest.mark.asyncio
async def test_a_qualification_failure_reports_failed_not_done(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A half-probed set must not read as a finished clean run."""
    monkeypatch.setattr(
        _seams,
        "execute_read",
        ReadRecorder(
            search=matches(("alpha", "A", None)),
            linked=read_error("RPC: AuthKeyUnregisteredError"),
        ),
    )
    monkeypatch.setattr(settings.neurocomment, "discovery_max_consecutive_errors", 1)
    await seed_listener()
    campaign_id = await new_campaign()

    await start_discovery(campaign_id, search_request())
    await drain_discovery(campaign_id)

    assert _discovery_state.phase_of(campaign_id) == "failed"
    assert _discovery_state.last_error(campaign_id) == "RPC: AuthKeyUnregisteredError"


@pytest.mark.asyncio
async def test_deleting_a_campaign_cancels_its_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Otherwise it keeps probing the shared listener for rows that no longer exist."""
    from services.neurocomment.campaigns import delete_campaign  # noqa: PLC0415

    running = asyncio.Event()

    async def _hang(*_args: object, **_kwargs: object) -> DiscoverySearchStageResult:
        running.set()
        await asyncio.Event().wait()
        return DiscoverySearchStageResult()

    monkeypatch.setattr(discovery_module, "run_search", _hang)
    await seed_listener()
    campaign_id = await new_campaign()

    await start_discovery(campaign_id, search_request())
    await running.wait()
    await delete_campaign(campaign_id)

    assert _discovery_state.is_running(campaign_id) is False
    assert _discovery_state.phase_of(campaign_id) == "idle"


@pytest.mark.asyncio
async def test_the_rolling_window_lets_the_allowance_recover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without pruning, the fleet is capped forever after the first 24h of searches."""
    monkeypatch.setattr(settings.neurocomment, "discovery_max_searches_per_day", 2)
    now = datetime.now(UTC)
    for index in range(2):
        assert _discovery_state.try_reserve(f"c{index}", f"acc-{index}", now) is None

    assert _discovery_state.at_daily_search_cap(now) is True
    assert _discovery_state.at_daily_search_cap(now + timedelta(hours=25)) is False


@pytest.mark.asyncio
async def test_an_unexpected_error_fails_the_run_without_escaping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A background task that raised into the void would leave phase stuck forever."""

    async def _boom(*_args: object, **_kwargs: object) -> DiscoverySearchStageResult:
        raise RuntimeError

    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches()))
    monkeypatch.setattr(discovery_module, "run_search", _boom)
    await seed_listener()
    campaign_id = await new_campaign()
    _discovery_state.set_run_report(
        campaign_id,
        DiscoveryRunReport(
            sources=[DiscoverySourceReport(source="telegram_search", state="ran", hits=9, kept=9)],
        ),
    )

    await start_discovery(campaign_id, search_request())
    await drain_discovery(campaign_id)

    assert _discovery_state.phase_of(campaign_id) == "failed"
    assert _discovery_state.last_error(campaign_id) == "RuntimeError"
    # The strip is per-run like the phase and the error: a crash never sets one, so
    # keeping the previous run's published a source report beside a run that made no
    # reads at all.
    assert _discovery_state.run_report(campaign_id).sources == []


@pytest.mark.asyncio
async def test_shutdown_cancels_an_in_flight_run(monkeypatch: pytest.MonkeyPatch) -> None:
    started = asyncio.Event()

    async def _hang(*_args: object, **_kwargs: object) -> DiscoverySearchStageResult:
        started.set()
        await asyncio.Event().wait()
        return DiscoverySearchStageResult()

    monkeypatch.setattr(discovery_module, "run_search", _hang)
    await seed_listener()
    campaign_id = await new_campaign()

    await start_discovery(campaign_id, search_request())
    await started.wait()
    await _discovery_state.shutdown_discovery_runs()

    assert _discovery_state.is_running(campaign_id) is False
