"""What the search stage TELLS the operator about a run, beside the rows it stored.

Its own module because ``test_discovery_search`` sits on the 700-line test cap. Every
case here is a sentence the board prints: how many channels a source reached, whether
the rows it is credited with exist, whether the list is a total or a ceiling, and
whether the subscriber bounds applied to a row at all.
"""

from __future__ import annotations

import pytest

from core.db import create_campaign
from schemas.neurocomment import CampaignCreate
from schemas.neurocomment_discovery import DiscoverySearchRequest, DiscoverySourceReport
from services.neurocomment import _seams
from services.neurocomment._discovery_search import run_search
from tests.services.neurocomment.discovery_support import (
    ReadRecorder,
    flood_error,
    matches,
    pool_of,
)

pytestmark = pytest.mark.usefixtures("isolate_discovery")


def _request(**overrides: object) -> DiscoverySearchRequest:
    payload: dict[str, object] = {"keywords": ["crypto"], "account_ids": ["acc-listener"]}
    payload.update(overrides)
    return DiscoverySearchRequest.model_validate(payload)


async def _new_campaign() -> str:
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    return campaign.campaign_id


def _report_of(sources: list[DiscoverySourceReport], source: str) -> DiscoverySourceReport:
    reports = [report for report in sources if report.source == source]
    assert len(reports) == 1
    return reports[0]


@pytest.mark.asyncio
async def test_hits_counts_the_channels_a_source_reached_not_its_result_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Summed per attempt, three keywords finding the same two channels read "2 of 6".

    The strip renders it as "kept of hits", so a merge that lost nothing looked like one
    that threw four rows away. The unusable handle is out of the denominator too: no
    operator filter controls it, so counting it overstated the loss again.
    """
    monkeypatch.setattr(
        _seams,
        "execute_read",
        ReadRecorder(search=matches(("alpha", "A", None), ("beta", "B", None), ("ab", "S", None))),
    )
    campaign_id = await _new_campaign()

    stage = await run_search(
        campaign_id,
        pool_of(),
        _request(keywords=["crypto", "trading", "markets"]),
    )

    search = _report_of(stage.report.sources, "telegram_search")
    assert (search.hits, search.kept) == (2, 2)


@pytest.mark.asyncio
async def test_a_run_that_stored_nothing_credits_no_kept_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A flood leaves the PREVIOUS run's rows in the table.

    Crediting this run's findings there described channels that exist nowhere, while the
    table below showed another search's and the header counted them as this run's find.
    """
    monkeypatch.setattr(
        _seams,
        "execute_read",
        ReadRecorder(search=matches(("alpha", "A", 100)), posts=flood_error(600)),
    )
    campaign_id = await _new_campaign()

    stage = await run_search(campaign_id, pool_of(), _request())

    assert stage.replaced is False
    search = _report_of(stage.report.sources, "telegram_search")
    # It did reach a channel — that is true whatever happened next — but it kept none.
    assert (search.hits, search.kept, search.exclusive) == (1, 0, 0)
    assert stage.report.stored is False
    assert stage.report.origins == {}


@pytest.mark.asyncio
async def test_the_candidate_cap_is_reported_as_the_ceiling_it_is(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A capped list is a floor, and "Channels found: 100" reads as everything there is."""
    monkeypatch.setattr(
        _seams,
        "execute_read",
        ReadRecorder(search=matches(("one", "1", None), ("two", "2", None), ("three", "3", None))),
    )
    campaign_id = await _new_campaign()

    stage = await run_search(campaign_id, pool_of(), _request(limit=2))

    assert (stage.found, stage.report.capped) == (2, True)


@pytest.mark.asyncio
async def test_a_set_that_fits_under_the_cap_is_not_reported_capped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _seams,
        "execute_read",
        ReadRecorder(search=matches(("one", "1", None), ("two", "2", None))),
    )
    campaign_id = await _new_campaign()

    stage = await run_search(campaign_id, pool_of(), _request(limit=2))

    assert (stage.found, stage.report.capped) == (2, False)


@pytest.mark.asyncio
async def test_a_row_the_bounds_never_applied_to_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Native search usually returns no count, so the bound admits the row unfiltered.

    Qualification then backfills the real number, and the board showed "300" in a list
    the operator asked to start at 10 000 with nothing to explain it.
    """
    monkeypatch.setattr(
        _seams,
        "execute_read",
        ReadRecorder(search=matches(("nocount", "N", None), ("big", "B", 50_000))),
    )
    campaign_id = await _new_campaign()

    stage = await run_search(campaign_id, pool_of(), _request(members_min=10_000))

    assert stage.report.origins["nocount"].uncounted is True
    # The bound genuinely applied to this one, so it claims nothing.
    assert stage.report.origins["big"].uncounted is False


@pytest.mark.asyncio
async def test_an_unfiltered_run_flags_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no bound typed there is no filter to have skipped, so the note would be noise."""
    monkeypatch.setattr(
        _seams, "execute_read", ReadRecorder(search=matches(("nocount", "N", None)))
    )
    campaign_id = await _new_campaign()

    stage = await run_search(campaign_id, pool_of(), _request())

    assert stage.report.origins["nocount"].uncounted is False
