"""Discovery stage 1 — source fan-out, merge/dedup and the candidate cap.

Account resolution and the start refusals live in ``test_discovery_account.py``
(700-line test cap).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from core.config import settings
from core.db import create_campaign
from core.repositories.neurocomment import list_discovery_candidates
from schemas.neurocomment import CampaignCreate
from schemas.neurocomment_discovery import (
    DiscoverySearchRequest,
    DiscoverySearchStageResult,
    DiscoverySourceReport,
)
from schemas.telegram_actions import LinkedDiscussionGroupResult
from schemas.telegram_actions_discovery import GlobalPostsCursor
from services.neurocomment import _discovery_waves, _seams
from services.neurocomment._discovery_search import run_search
from services.neurocomment._state import in_cooldown
from tests.services.neurocomment.discovery_support import (
    LISTENER_ID,
    ReadRecorder,
    flood_error,
    matches,
    posts_page,
    read_error,
)

pytestmark = pytest.mark.usefixtures("isolate_discovery")


def _request(**overrides: object) -> DiscoverySearchRequest:
    payload: dict[str, object] = {"keywords": ["crypto"]}
    payload.update(overrides)
    return DiscoverySearchRequest.model_validate(payload)


async def _new_campaign() -> str:
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    return campaign.campaign_id


def _report_of(stage: DiscoverySearchStageResult, source: str) -> DiscoverySourceReport:
    reports = [report for report in stage.report.sources if report.source == source]
    assert len(reports) == 1
    return reports[0]


@pytest.mark.asyncio
async def test_native_search_runs_once_per_keyword(monkeypatch: pytest.MonkeyPatch) -> None:
    reader = ReadRecorder(search=matches(("alpha", "Alpha", 100)))
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await _new_campaign()

    stage = await run_search(campaign_id, LISTENER_ID, _request(keywords=["crypto", "trading"]))

    assert stage.error is None
    assert stage.found == 1  # both keywords returned the same channel -> deduped
    queries = [action.query for action in reader.search_actions()]
    assert queries == ["crypto", "trading"]


@pytest.mark.asyncio
async def test_duplicate_keywords_are_searched_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the SPA deduped, so a direct caller spent ten RPCs on one keyword."""
    reader = ReadRecorder(search=matches(("alpha", "Alpha", 100)))
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await _new_campaign()

    await run_search(
        campaign_id,
        LISTENER_ID,
        _request(keywords=[" Crypto ", "crypto", "CRYPTO"]),
    )

    assert [action.query for action in reader.search_actions()] == ["Crypto"]


@pytest.mark.asyncio
async def test_seed_channel_adds_a_similar_channels_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = ReadRecorder(
        search=matches(("fromsearch", "S", None)),
        similar=matches(("fromsimilar", "R", None)),
    )
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await _new_campaign()

    stage = await run_search(campaign_id, LISTENER_ID, _request(seed_channel="@durov"))

    assert stage.found == 2
    # Normalized before it reaches the gateway, the same way every other channel is.
    # The operator's seed is read first; the wave over the sweep's own hits follows it.
    assert [action.seed for action in reader.similar_actions()] == ["durov", "fromsearch"]


@pytest.mark.asyncio
async def test_an_unusable_seed_is_reported_instead_of_probed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A seed that survives validation but not normalization cost an RPC and said nothing."""
    reader = ReadRecorder(search=matches(("alpha", "A", None)))
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await _new_campaign()

    stage = await run_search(campaign_id, LISTENER_ID, _request(seed_channel="t.me/c/12345/1"))

    # The unusable seed itself was never sent; the wave's own seeds are the sweep's hits.
    assert [action.seed for action in reader.similar_actions()] == ["alpha"]
    assert _report_of(stage, "telegram_similar").state == "skipped"
    assert stage.error == "seed_unusable"


@pytest.mark.asyncio
async def test_the_seed_pass_and_the_wave_report_separately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One row for both would let whichever ran mask the other's reason.

    Folded together, a wave that answered flips the row to ``ran`` and the operator never
    learns their own seed was unusable.
    """
    monkeypatch.setattr(
        _seams,
        "execute_read",
        ReadRecorder(search=matches(("alpha", "A", None)), similar=matches(("beta", "B", None))),
    )
    campaign_id = await _new_campaign()

    stage = await run_search(campaign_id, LISTENER_ID, _request(seed_channel="t.me/c/12345/1"))

    seed_pass = _report_of(stage, "telegram_similar")
    assert (seed_pass.state, seed_pass.reason) == ("skipped", "seed_unusable")
    assert _report_of(stage, "telegram_recommended").state == "ran"


@pytest.mark.asyncio
async def test_the_wave_asks_recommendations_for_the_sweeps_own_hits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Telegram's recommendation graph reaches channels no keyword named.

    It needs no seed from the operator: the keyword sweep's own hits are the seeds.
    """
    reader = ReadRecorder(
        search=matches(("alpha", "A", None)),
        similar=matches(("recommended", "R", None)),
    )
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await _new_campaign()

    stage = await run_search(campaign_id, LISTENER_ID, _request())

    assert [action.seed for action in reader.similar_actions()] == ["alpha"]
    rows = {row.channel: row.source for row in (await list_discovery_candidates(campaign_id)).rows}
    assert rows == {"alpha": "telegram_search", "recommended": "telegram_recommended"}
    assert _report_of(stage, "telegram_recommended").exclusive == 1
    assert stage.error is None


@pytest.mark.asyncio
async def test_the_wave_seeds_the_biggest_hits_first_and_stops_at_its_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One read per seed multiplies, so the wave is bounded — biggest channels first."""
    monkeypatch.setattr(_discovery_waves, "_SIMILAR_FROM_TOP", 2)
    reader = ReadRecorder(
        search=matches(("small", "S", 10), ("biggest", "B", 900), ("middle", "M", 100)),
    )
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await _new_campaign()

    await run_search(campaign_id, LISTENER_ID, _request())

    assert [action.seed for action in reader.similar_actions()] == ["biggest", "middle"]


@pytest.mark.asyncio
async def test_a_flood_wait_stops_the_recommendation_wave(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wave is cheap, not exempt: it obeys the same flood rule as the keyword sweep."""
    reader = ReadRecorder(
        search=matches(("alpha", "A", 30), ("bravo", "B", 20), ("charlie", "C", 10)),
        similar=flood_error(900),
    )
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await _new_campaign()

    stage = await run_search(campaign_id, LISTENER_ID, _request())

    assert len(reader.similar_actions()) == 1
    assert stage.flooded is True
    assert in_cooldown(LISTENER_ID, datetime.now(UTC)) is True


@pytest.mark.asyncio
async def test_the_keyword_search_wins_a_cross_source_tie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The higher-priority source's spelling is what adopt writes into the campaign."""
    reader = ReadRecorder(
        search=matches(("CryptoNews", "Search", 500)),
        similar=matches(("cryptonews", "Similar", 999)),
    )
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await _new_campaign()

    stage = await run_search(campaign_id, LISTENER_ID, _request(seed_channel="@durov"))

    rows = (await list_discovery_candidates(campaign_id)).rows
    assert [row.channel for row in rows] == ["CryptoNews"]
    assert rows[0].source == "telegram_search"
    assert rows[0].title == "Search"
    # Every source that returned it is credited: crediting only the dedup winner
    # under-reported the losers, which is the very signal that made starvation invisible.
    assert stage.report.origins["CryptoNews"].sources == [
        "telegram_search",
        "telegram_similar",
        "telegram_recommended",
    ]
    assert _report_of(stage, "telegram_similar").kept == 1


@pytest.mark.asyncio
async def test_a_source_reports_what_it_alone_contributed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``kept`` credits every source that returned a row, which hid a starvation variant.

    A source whose rows were mostly duplicates of the other's reported a healthy ``kept``
    while every channel it found alone was cut by the cap.
    """
    # Two sources only: the wave reads the same recommendation stub, so its rows would
    # co-credit the seed pass's and this accounting has nothing to do with the wave.
    monkeypatch.setattr(_discovery_waves, "_SIMILAR_FROM_TOP", 0)
    monkeypatch.setattr(
        _seams,
        "execute_read",
        ReadRecorder(
            search=matches(("shared", "S", 10)),
            similar=matches(("shared", "S", 10), ("mineonly", "M", 20)),
        ),
    )
    campaign_id = await _new_campaign()

    stage = await run_search(campaign_id, LISTENER_ID, _request(seed_channel="@durov"))

    similar = _report_of(stage, "telegram_similar")
    assert similar.kept == 2
    assert similar.exclusive == 1
    assert _report_of(stage, "telegram_search").exclusive == 0


@pytest.mark.asyncio
async def test_lower_priority_rows_survive_a_sweep_that_fills_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sorting the union by source priority made the cap a keyword-search-only cut.

    The similar pass sits last, so with 10 keyword rows against a cap of 4 it contributed
    exactly zero and the seed influenced nothing at all.
    """
    monkeypatch.setattr(settings.neurocomment, "discovery_max_candidates", 4)
    monkeypatch.setattr(
        _seams,
        "execute_read",
        ReadRecorder(
            search=matches(*((f"native{index}", "N", None) for index in range(10))),
            similar=matches(("lookalike1", "L", 900), ("lookalike2", "L", 900)),
        ),
    )
    campaign_id = await _new_campaign()

    stage = await run_search(campaign_id, LISTENER_ID, _request(seed_channel="@durov"))

    stored = {row.channel for row in (await list_discovery_candidates(campaign_id)).rows}
    assert len(stored) == 4
    assert stored & {"lookalike1", "lookalike2"}
    assert _report_of(stage, "telegram_similar").kept


@pytest.mark.asyncio
async def test_native_read_failure_does_not_abort_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = ReadRecorder(
        search=read_error("RPC: ChannelPrivateError"),
        similar=matches(("recovered", "R", None)),
    )
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await _new_campaign()

    stage = await run_search(campaign_id, LISTENER_ID, _request(seed_channel="seed"))

    assert stage.found == 1
    assert stage.error == "RPC: ChannelPrivateError"
    assert _report_of(stage, "telegram_search").state == "failed"


@pytest.mark.asyncio
async def test_an_unexpected_gateway_shape_is_not_an_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Otherwise a Telethon-layer change wipes the stored set through the empty replace."""
    monkeypatch.setattr(
        _seams,
        "execute_read",
        ReadRecorder(search=LinkedDiscussionGroupResult(linked_chat_id=-1, comments_enabled=True)),
    )
    campaign_id = await _new_campaign()

    stage = await run_search(campaign_id, LISTENER_ID, _request())

    assert stage.replaced is False
    assert stage.error == "unexpected_result"


@pytest.mark.asyncio
async def test_an_empty_wave_cannot_overrule_a_failed_keyword_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty replace needs the sweep that DEFINES the run to have answered.

    The wider waves are consulted on every run now, so "somebody answered" alone stopped
    protecting the stored set: a keyword sweep that merely timed out would hand the
    delete-then-insert to an empty page from a narrower index, wiping candidates the
    operator had already reviewed and qualified.
    """
    monkeypatch.setattr(
        _seams,
        "execute_read",
        ReadRecorder(search=read_error("RPC: Timeout"), posts=posts_page()),
    )
    campaign_id = await _new_campaign()

    stage = await run_search(campaign_id, LISTENER_ID, _request())

    assert stage.replaced is False
    assert stage.error == "RPC: Timeout"


@pytest.mark.asyncio
async def test_rows_from_a_wave_replace_even_when_the_sweep_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The counterpart: found channels ARE this run's findings, whoever found them.

    Keeping the previous set beside them would present another keyword set's channels as
    this run's, ticked and adoptable.
    """
    monkeypatch.setattr(
        _seams,
        "execute_read",
        ReadRecorder(
            search=read_error("RPC: Timeout"),
            posts=posts_page(("fromposts", "P", 400)),
        ),
    )
    campaign_id = await _new_campaign()

    stage = await run_search(campaign_id, LISTENER_ID, _request())

    assert stage.replaced is True
    assert [row.channel for row in (await list_discovery_candidates(campaign_id)).rows] == [
        "fromposts",
    ]


@pytest.mark.asyncio
async def test_a_flood_wait_stops_the_keyword_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every further read lands inside the live window, and Telegram escalates repeats."""
    reader = ReadRecorder(search=flood_error(1800), similar=matches(("x", "X", None)))
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await _new_campaign()

    stage = await run_search(
        campaign_id,
        LISTENER_ID,
        _request(keywords=["alpha", "bravo", "charlie"], seed_channel="@durov"),
    )

    # One attempt, then stop — not one per keyword, and every later wave is skipped too.
    assert len(reader.search_actions()) == 1
    assert reader.posts_actions() == []
    assert reader.actions_of("get_similar_channels") == []
    assert stage.flooded is True
    # EVERY unreached source is named on the board rather than silently absent from it —
    # the post wave included, which used to vanish from the strip entirely.
    unreached = [row for row in stage.report.sources if row.source != "telegram_search"]
    assert [(row.source, row.state) for row in unreached] == [
        ("telegram_posts", "skipped"),
        ("telegram_similar", "skipped"),
        ("telegram_recommended", "skipped"),
    ]
    # And a run cut short mid-sweep does not hand its fragment to the delete-then-insert.
    assert stage.replaced is False


@pytest.mark.asyncio
async def test_a_flood_wait_puts_the_account_on_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Discovery read both fleet flood signals but wrote neither, so its own was invisible."""
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=flood_error(600)))
    campaign_id = await _new_campaign()

    await run_search(campaign_id, LISTENER_ID, _request())

    assert in_cooldown(LISTENER_ID, datetime.now(UTC)) is True


@pytest.mark.asyncio
async def test_member_bounds_are_inclusive(monkeypatch: pytest.MonkeyPatch) -> None:
    """A channel sitting exactly on the operator's bound belongs in the result."""
    monkeypatch.setattr(
        _seams,
        "execute_read",
        ReadRecorder(search=matches(("atmin", "Min", 1_000), ("atmax", "Max", 100_000))),
    )
    campaign_id = await _new_campaign()

    await run_search(
        campaign_id,
        LISTENER_ID,
        _request(members_min=1_000, members_max=100_000),
    )

    rows = (await list_discovery_candidates(campaign_id)).rows
    assert sorted(row.channel for row in rows) == ["atmax", "atmin"]


@pytest.mark.asyncio
async def test_member_bounds_filter_hits_with_a_known_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = ReadRecorder(
        search=matches(("tiny", "T", 10), ("right", "R", 5_000), ("huge", "H", 900_000)),
    )
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await _new_campaign()

    await run_search(
        campaign_id,
        LISTENER_ID,
        _request(members_min=1_000, members_max=100_000),
    )

    rows = (await list_discovery_candidates(campaign_id)).rows
    assert [row.channel for row in rows] == ["right"]


@pytest.mark.asyncio
async def test_unknown_member_count_is_kept_not_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Native search rarely returns a count; qualification fills it in later."""
    reader = ReadRecorder(search=matches(("nocount", "N", None)))
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await _new_campaign()

    await run_search(campaign_id, LISTENER_ID, _request(members_min=1_000))

    rows = (await list_discovery_candidates(campaign_id)).rows
    assert [row.channel for row in rows] == ["nocount"]


@pytest.mark.asyncio
async def test_a_count_from_one_source_filters_the_same_channel_from_another(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dedup winner may carry no count, so counts must pool before dedup.

    Otherwise the preferred spelling shadows the only count anyone knew, and the
    channel slips through a filter it plainly fails.
    """
    monkeypatch.setattr(
        _seams,
        "execute_read",
        ReadRecorder(search=matches(("small", "S", None)), similar=matches(("small", "S", 42))),
    )
    campaign_id = await _new_campaign()

    await run_search(
        campaign_id,
        LISTENER_ID,
        _request(seed_channel="@durov", members_min=10_000),
    )

    assert (await list_discovery_candidates(campaign_id)).rows == []


@pytest.mark.asyncio
async def test_a_pooled_count_is_stored_on_the_surviving_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _seams,
        "execute_read",
        ReadRecorder(search=matches(("big", "B", None)), similar=matches(("big", "B", 50_000))),
    )
    campaign_id = await _new_campaign()

    await run_search(campaign_id, LISTENER_ID, _request(seed_channel="@durov"))

    rows = (await list_discovery_candidates(campaign_id)).rows
    # The keyword search still wins the spelling and the source label; only the count
    # is borrowed.
    assert rows[0].source == "telegram_search"
    assert rows[0].subscribers == 50_000


@pytest.mark.asyncio
async def test_candidate_cap_truncates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.neurocomment, "discovery_max_candidates", 2)
    reader = ReadRecorder(
        search=matches(("one", "1", None), ("two", "2", None), ("three", "3", None)),
    )
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await _new_campaign()

    stage = await run_search(campaign_id, LISTENER_ID, _request())

    assert stage.found == 2


@pytest.mark.asyncio
async def test_unnormalizable_handles_are_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    reader = ReadRecorder(
        search=matches(
            ("ab", "too short", None), ("bad-handle", "illegal", None), ("ok1", "K", None)
        ),
    )
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await _new_campaign()

    await run_search(campaign_id, LISTENER_ID, _request())

    rows = (await list_discovery_candidates(campaign_id)).rows
    assert [row.channel for row in rows] == ["ok1"]


@pytest.mark.asyncio
async def test_every_read_after_the_first_is_paced(monkeypatch: pytest.MonkeyPatch) -> None:
    """A burst is the freeze vector, so the wider waves are jittered like the sweep."""
    slept: list[float] = []

    async def _record(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(_seams, "sleep", _record)
    monkeypatch.setattr(settings.neurocomment, "discovery_qualify_delay_min_seconds", 1.5)
    monkeypatch.setattr(settings.neurocomment, "discovery_qualify_delay_max_seconds", 1.5)
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches()))
    campaign_id = await _new_campaign()

    await run_search(campaign_id, LISTENER_ID, _request(keywords=["alpha", "beta", "gamma"]))

    # Three keywords → two gaps (the first read is not preceded by a pause), then one
    # global page per keyword, each paced. The sweep found nothing, so no wave seeds.
    assert slept == [1.5] * 5


@pytest.mark.asyncio
async def test_the_post_search_contributes_its_own_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A different index: channels whose POSTS match, whatever their title says."""
    reader = ReadRecorder(
        search=matches(("namedcrypto", "Named", None)),
        posts=posts_page(("quietchannel", "Quiet", 500)),
    )
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await _new_campaign()

    stage = await run_search(campaign_id, LISTENER_ID, _request())

    assert [action.query for action in reader.posts_actions()] == ["crypto"]
    rows = {row.channel: row.source for row in (await list_discovery_candidates(campaign_id)).rows}
    assert rows["quietchannel"] == "telegram_posts"
    assert _report_of(stage, "telegram_posts").exclusive == 1


@pytest.mark.asyncio
async def test_the_post_search_pages_on_the_cursor_up_to_its_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The search never says "done" — only the page bound ends a keyword's paging.

    ``limit`` counts messages, not channels, so a short page is not an end-of-results
    signal either; the cursor is followed until the budget runs out.
    """
    monkeypatch.setattr(_discovery_waves, "_GLOBAL_MAX_PAGES", 2)
    first = posts_page(
        ("first", "1", None),
        cursor=GlobalPostsCursor(offset_rate=7, peer="first"),
    )
    second = posts_page(
        ("second", "2", None),
        cursor=GlobalPostsCursor(offset_rate=9, peer="second"),
    )
    reader = ReadRecorder(posts=lambda action: first if action.cursor is None else second)
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await _new_campaign()

    await run_search(campaign_id, LISTENER_ID, _request())

    cursors = [action.cursor for action in reader.posts_actions()]
    # First page starts fresh; the second carries page one's cursor back verbatim.
    assert cursors == [None, GlobalPostsCursor(offset_rate=7, peer="first")]
    stored = {row.channel for row in (await list_discovery_candidates(campaign_id)).rows}
    assert stored == {"first", "second"}


@pytest.mark.asyncio
async def test_paging_stops_on_a_page_that_adds_no_new_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeat posts from the same channels are the only end signal Telegram gives us."""
    monkeypatch.setattr(_discovery_waves, "_GLOBAL_MAX_PAGES", 5)
    repeat = posts_page(("same", "S", None), cursor=GlobalPostsCursor(offset_rate=1, peer="same"))
    reader = ReadRecorder(posts=repeat)
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await _new_campaign()

    await run_search(campaign_id, LISTENER_ID, _request())

    assert len(reader.posts_actions()) == 2


@pytest.mark.asyncio
async def test_the_run_read_budget_truncates_the_wider_waves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One budget across every wave, spent cheapest-first — and reported when it runs out.

    The waves multiply reads on a single account, so the run bounds the total. The cheap
    keyword sweep is served first; what is left reaches the post pages and the wave.
    """
    monkeypatch.setattr(settings.neurocomment, "discovery_max_reads_per_run", 2)
    reader = ReadRecorder(search=matches(("alpha", "A", None)))
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await _new_campaign()

    stage = await run_search(
        campaign_id,
        LISTENER_ID,
        _request(keywords=["alpha", "bravo", "charlie"]),
    )

    assert len(reader.search_actions()) == 2
    assert reader.posts_actions() == []
    assert reader.actions_of("get_similar_channels") == []
    # Truncated, not exhausted: the operator is told the run stopped asking.
    search = _report_of(stage, "telegram_search")
    assert (search.state, search.truncated) == ("ran", True)
    assert _report_of(stage, "telegram_posts").truncated is True
    assert _report_of(stage, "telegram_recommended").reason == "read_budget"
    # NOT the run's error: a spent budget is truncation, and every source answered or
    # said why it did not. Reporting it as the error painted the DEFAULT case — a full
    # keyword list always outruns the budget — as a degraded, red run.
    assert stage.error is None
    assert stage.replaced is True
