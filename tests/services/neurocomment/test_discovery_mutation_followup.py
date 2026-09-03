"""Behavioral contracts for the discovery board while a run is in flight."""

from __future__ import annotations

import asyncio

import pytest

from core.repositories.neurocomment import replace_discovery_candidates
from schemas.neurocomment_discovery import DiscoveryCandidateRow, DiscoverySearchStageResult
from services.neurocomment import _discovery_run
from services.neurocomment.discovery import load_discovery
from tests.services.neurocomment.discovery_support import (
    drain_discovery,
    new_campaign,
    search_request,
    seed_listener,
    start_run,
)

pytestmark = pytest.mark.usefixtures("isolate_discovery")


@pytest.mark.asyncio
async def test_board_preserves_candidate_details_during_qualification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The polling board remains complete and truthful while qualification blocks.

    A real run spends most of its lifetime in qualification. During that window the
    operator must see both the active phase and the exact search result they may later
    adopt; schema defaults must not silently replace persisted metadata.
    """
    qualification_started = asyncio.Event()
    release_qualification = asyncio.Event()
    qualification_calls: list[str] = []

    async def _search(
        _campaign_id: str,
        _pool: object,
        _request: object,
    ) -> DiscoverySearchStageResult:
        return DiscoverySearchStageResult(found=1, replaced=True)

    async def _qualify(campaign_id: str, _pool: object, _request: object) -> None:
        qualification_calls.append(campaign_id)
        qualification_started.set()
        await release_qualification.wait()

    monkeypatch.setattr(_discovery_run, "run_search", _search)
    monkeypatch.setattr(_discovery_run, "run_qualification", _qualify)
    await seed_listener()
    campaign_id = await new_campaign()
    await replace_discovery_candidates(
        campaign_id,
        [
            DiscoveryCandidateRow(
                channel="signal_room",
                title="Signal Room",
                subscribers=4321,
                source="telegram_search",
            ),
        ],
    )

    outcome = await start_run(campaign_id, search_request())
    assert outcome.status == "started"
    await asyncio.wait_for(qualification_started.wait(), timeout=2.0)

    try:
        board = await load_discovery(campaign_id)

        assert board is not None
        assert board.campaign_id == campaign_id
        assert board.progress.phase == "qualifying"
        assert board.progress.running is True
        assert board.progress.total == 1
        assert qualification_calls == [campaign_id]
        assert len(board.candidates) == 1
        candidate = board.candidates[0]
        assert candidate.channel == "signal_room"
        assert candidate.title == "Signal Room"
        assert candidate.subscribers == 4321
        assert candidate.source == "telegram_search"
    finally:
        release_qualification.set()
        await asyncio.wait_for(drain_discovery(campaign_id), timeout=2.0)

    finished = await load_discovery(campaign_id)
    assert finished is not None
    assert finished.progress.phase == "done"
    assert finished.progress.running is False
