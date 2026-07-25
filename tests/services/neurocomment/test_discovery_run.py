"""Discovery run lifecycle — single-flight, the search allowance, and the phase machine.

``_run`` is what the whole UI reads through ``DiscoveryBoard.progress``, and the
start path claims a slot before it awaits. Both are covered here rather than in the
stage files, because both are about the run as a whole.
"""

from __future__ import annotations

import asyncio

import pytest

from core.config import settings
from core.repositories.neurocomment import (
    list_discovery_candidates,
    replace_discovery_candidates,
)
from schemas.neurocomment_discovery import DiscoveryCandidateRow
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

pytestmark = pytest.mark.usefixtures("isolate_discovery")


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
    monkeypatch.setattr(_seams, "execute_read", reader)
    await seed_listener()
    campaign_id = await new_campaign()

    first, second = await asyncio.gather(
        start_discovery(campaign_id, search_request()),
        start_discovery(campaign_id, search_request()),
    )
    await drain_discovery(campaign_id)

    assert first is not None
    assert second is not None
    assert {first.status, second.status} == {"started", "already_running"}
    # One keyword, one run: a second stream would show up as a second search RPC.
    assert len(reader.search_actions()) == 1


@pytest.mark.asyncio
async def test_concurrent_starts_cannot_overrun_the_daily_allowance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cap is fleet-wide, so two campaigns racing at 0/1 must not both pass."""
    monkeypatch.setattr(settings.neurocomment, "discovery_max_searches_per_day", 1)
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches()))
    await seed_listener()
    first_campaign = await new_campaign()
    second_campaign = await new_campaign()

    first, second = await asyncio.gather(
        start_discovery(first_campaign, search_request()),
        start_discovery(second_campaign, search_request()),
    )
    await drain_discovery(first_campaign)
    await drain_discovery(second_campaign)

    assert first is not None
    assert second is not None
    assert {first.status, second.status} == {"started", "daily_limit_reached"}


@pytest.mark.asyncio
async def test_a_refused_start_releases_the_slot_it_claimed() -> None:
    """The claim is taken before the account check, so a refusal must give it back."""
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
async def test_an_unexpected_error_fails_the_run_without_escaping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A background task that raised into the void would leave phase stuck forever."""

    async def _boom(*_args: object, **_kwargs: object) -> tuple[int, str | None]:
        raise RuntimeError

    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches()))
    monkeypatch.setattr(discovery_module, "run_search", _boom)
    await seed_listener()
    campaign_id = await new_campaign()

    await start_discovery(campaign_id, search_request())
    await drain_discovery(campaign_id)

    assert _discovery_state.phase_of(campaign_id) == "failed"
    assert _discovery_state.last_error(campaign_id) == "RuntimeError"


@pytest.mark.asyncio
async def test_shutdown_cancels_an_in_flight_run(monkeypatch: pytest.MonkeyPatch) -> None:
    started = asyncio.Event()

    async def _hang(*_args: object, **_kwargs: object) -> tuple[int, str | None]:
        started.set()
        await asyncio.Event().wait()
        return 0, None

    monkeypatch.setattr(discovery_module, "run_search", _hang)
    await seed_listener()
    campaign_id = await new_campaign()

    await start_discovery(campaign_id, search_request())
    await started.wait()
    await _discovery_state.shutdown_discovery_runs()

    assert _discovery_state.is_running(campaign_id) is False
