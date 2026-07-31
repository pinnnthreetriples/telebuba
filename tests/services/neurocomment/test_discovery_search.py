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
from schemas.telemetr import TelemetrSearchRequest, TelemetrSearchResult
from services.neurocomment import _seams
from services.neurocomment._discovery_search import run_search
from services.neurocomment._state import in_cooldown
from tests.services.neurocomment.discovery_support import (
    LISTENER_ID,
    ReadRecorder,
    TelemetrRecorder,
    matches,
    read_error,
    telemetr_ok,
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
    telemetr = TelemetrRecorder(telemetr_ok())
    monkeypatch.setattr(_seams, "search_telemetr", telemetr)
    campaign_id = await _new_campaign()

    await run_search(
        campaign_id,
        LISTENER_ID,
        _request(keywords=[" Crypto ", "crypto", "CRYPTO"], use_telemetr=True),
    )

    assert [action.query for action in reader.search_actions()] == ["Crypto"]
    assert len(telemetr.requests) == 1


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
    assert [action.seed for action in reader.similar_actions()] == ["durov"]


@pytest.mark.asyncio
async def test_an_unusable_seed_is_reported_instead_of_probed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A seed that survives validation but not normalization cost an RPC and said nothing."""
    reader = ReadRecorder(search=matches(("alpha", "A", None)))
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await _new_campaign()

    stage = await run_search(campaign_id, LISTENER_ID, _request(seed_channel="t.me/c/12345/1"))

    assert reader.actions_of("get_similar_channels") == []
    assert _report_of(stage, "telegram_similar").state == "skipped"
    assert stage.error == "seed_unusable"


@pytest.mark.asyncio
async def test_no_seed_means_no_recommendations_call(monkeypatch: pytest.MonkeyPatch) -> None:
    reader = ReadRecorder(search=matches(("alpha", "A", None)))
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await _new_campaign()

    stage = await run_search(campaign_id, LISTENER_ID, _request())

    assert reader.actions_of("get_similar_channels") == []
    assert stage.error is None


@pytest.mark.asyncio
async def test_native_wins_a_cross_source_tie(monkeypatch: pytest.MonkeyPatch) -> None:
    """Telegram's spelling is canonical — adopt writes it into the campaign verbatim."""
    reader = ReadRecorder(search=matches(("CryptoNews", "Native", 500)))
    telemetr = TelemetrRecorder(telemetr_ok(("cryptonews", "Catalogue", 999)))
    monkeypatch.setattr(_seams, "execute_read", reader)
    monkeypatch.setattr(_seams, "search_telemetr", telemetr)
    campaign_id = await _new_campaign()

    stage = await run_search(campaign_id, LISTENER_ID, _request(use_telemetr=True))

    rows = (await list_discovery_candidates(campaign_id)).rows
    assert [row.channel for row in rows] == ["CryptoNews"]
    assert rows[0].source == "telegram_search"
    assert rows[0].title == "Native"
    # Both sources are credited for it: crediting only the dedup winner under-reported
    # the catalogue, which is the very signal that made its starvation invisible.
    assert stage.report.origins["CryptoNews"].sources == ["telegram_search", "telemetr"]
    assert _report_of(stage, "telemetr").kept == 1


@pytest.mark.asyncio
async def test_telemetr_is_not_called_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    telemetr = TelemetrRecorder(telemetr_ok(("never", "N", None)))
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches()))
    monkeypatch.setattr(_seams, "search_telemetr", telemetr)
    campaign_id = await _new_campaign()

    stage = await run_search(campaign_id, LISTENER_ID, _request(use_telemetr=False))

    assert telemetr.requests == []
    assert _report_of(stage, "telemetr").state == "skipped"


@pytest.mark.asyncio
async def test_a_source_reports_what_it_alone_contributed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``kept`` credits every source that returned a row, which hid a starvation variant.

    A catalogue whose rows were mostly duplicates of native hits reported a healthy
    ``kept`` while every channel it found alone was cut by the cap.
    """
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches(("shared", "S", 10))))
    monkeypatch.setattr(
        _seams,
        "search_telemetr",
        TelemetrRecorder(telemetr_ok(("shared", "S", 10), ("mineonly", "M", 20))),
    )
    campaign_id = await _new_campaign()

    stage = await run_search(campaign_id, LISTENER_ID, _request(use_telemetr=True))

    catalogue = _report_of(stage, "telemetr")
    assert catalogue.kept == 2
    assert catalogue.exclusive == 1
    assert _report_of(stage, "telegram_search").exclusive == 0


@pytest.mark.asyncio
async def test_catalogue_only_drops_the_telegram_arm_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every stored row is then locale-verified, and the board names the missing sources.

    Measured on the real cap: with several keywords this costs no rows at all and takes
    the verified share from about half to all of them. A skipped source that reported
    nothing is what made the original bug invisible, so both Telegram arms still report.
    """
    reader = ReadRecorder(search=matches(("nativerow", "N", 500)))
    monkeypatch.setattr(_seams, "execute_read", reader)
    monkeypatch.setattr(
        _seams,
        "search_telemetr",
        TelemetrRecorder(telemetr_ok(("turkishnews", "TR", 900), language="tr", country="TR")),
    )
    campaign_id = await _new_campaign()

    stage = await run_search(
        campaign_id,
        LISTENER_ID,
        _request(use_telemetr=True, catalogue_only=True, language="tr", country="TR"),
    )

    assert reader.search_actions() == []
    assert [row.channel for row in (await list_discovery_candidates(campaign_id)).rows] == [
        "turkishnews",
    ]
    assert _report_of(stage, "telegram_search").state == "skipped"
    assert _report_of(stage, "telegram_similar").state == "skipped"
    assert _report_of(stage, "telemetr").kept == 1


@pytest.mark.asyncio
async def test_telemetr_filters_reach_the_stored_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """The filters must change the *result*, not just the DTO handed to the gateway.

    Asserting the request object alone passed identically whether the catalogue rows
    reached the candidate table or were dropped on the floor.
    """
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches()))

    async def _by_language(request: TelemetrSearchRequest) -> TelemetrSearchResult:
        if request.language == "tr":
            return telemetr_ok(("turkishnews", "TR", 900), language="tr", country="TR")
        return telemetr_ok(("russiannews", "RU", 900), language="ru", country="RU")

    monkeypatch.setattr(_seams, "search_telemetr", _by_language)
    filtered = await _new_campaign()
    unfiltered = await _new_campaign()

    await run_search(
        filtered,
        LISTENER_ID,
        _request(use_telemetr=True, language="tr", country="TR"),
    )
    await run_search(unfiltered, LISTENER_ID, _request(use_telemetr=True))

    assert [row.channel for row in (await list_discovery_candidates(filtered)).rows] == [
        "turkishnews",
    ]
    assert [row.channel for row in (await list_discovery_candidates(unfiltered)).rows] == [
        "russiannews",
    ]


@pytest.mark.asyncio
async def test_the_catalogue_geo_rides_onto_the_run_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches()))
    monkeypatch.setattr(
        _seams,
        "search_telemetr",
        TelemetrRecorder(telemetr_ok(("turkishnews", "TR", 900), language="tr", country="TR")),
    )
    campaign_id = await _new_campaign()

    stage = await run_search(
        campaign_id,
        LISTENER_ID,
        _request(use_telemetr=True, language="tr", country="TR"),
    )

    origin = stage.report.origins["turkishnews"]
    assert (origin.country, origin.language) == ("TR", "tr")


@pytest.mark.asyncio
async def test_catalogue_rows_survive_a_native_sweep_that_fills_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sorting the union by source priority made the cap a native-only cut.

    Telemetr sits last, so with 200 native rows against a cap of 100 it contributed
    exactly zero and language/country influenced nothing at all.
    """
    monkeypatch.setattr(settings.neurocomment, "discovery_max_candidates", 4)
    monkeypatch.setattr(
        _seams,
        "execute_read",
        ReadRecorder(search=matches(*((f"native{index}", "N", None) for index in range(10)))),
    )
    monkeypatch.setattr(
        _seams,
        "search_telemetr",
        TelemetrRecorder(telemetr_ok(("catalogue1", "C", 900), ("catalogue2", "C", 900))),
    )
    campaign_id = await _new_campaign()

    stage = await run_search(campaign_id, LISTENER_ID, _request(use_telemetr=True))

    stored = {row.channel for row in (await list_discovery_candidates(campaign_id)).rows}
    assert len(stored) == 4
    assert stored & {"catalogue1", "catalogue2"}
    assert _report_of(stage, "telemetr").kept


@pytest.mark.asyncio
async def test_missing_telemetr_key_is_a_skip_the_operator_is_told_about(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unconfigured catalogue is a skipped source, not a failure.

    But a *silent* skip let the run reach "done" while the operator believed the filter
    they ticked had applied, against a catalogue that was never queried.
    """
    telemetr = TelemetrRecorder(TelemetrSearchResult(status="not_configured"))
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches(("alpha1", "A", None))))
    monkeypatch.setattr(_seams, "search_telemetr", telemetr)
    campaign_id = await _new_campaign()

    stage = await run_search(campaign_id, LISTENER_ID, _request(use_telemetr=True))

    assert stage.found == 1
    assert stage.error == "telemetr_not_configured"
    assert _report_of(stage, "telemetr").state == "skipped"


@pytest.mark.asyncio
async def test_a_catalogue_failure_keeps_its_diagnostic_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``telemetr_auth_failed`` alone cannot tell a revoked key from a dead network."""
    telemetr = TelemetrRecorder(
        TelemetrSearchResult(status="auth_failed", error="HTTP 401: invalid api key"),
    )
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches(("native", "N", 10))))
    monkeypatch.setattr(_seams, "search_telemetr", telemetr)
    campaign_id = await _new_campaign()

    stage = await run_search(campaign_id, LISTENER_ID, _request(use_telemetr=True))

    report = _report_of(stage, "telemetr")
    assert report.state == "failed"
    assert report.reason == "telemetr_auth_failed"
    assert report.detail == "HTTP 401: invalid api key"


@pytest.mark.asyncio
async def test_telemetr_rate_limit_keeps_native_results_and_reports_the_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telemetr = TelemetrRecorder(TelemetrSearchResult(status="rate_limited", error="HTTP 429"))
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches(("native", "N", 10))))
    monkeypatch.setattr(_seams, "search_telemetr", telemetr)
    campaign_id = await _new_campaign()

    stage = await run_search(campaign_id, LISTENER_ID, _request(use_telemetr=True))

    assert stage.found == 1
    assert stage.error == "telemetr_rate_limited"


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
        ReadRecorder(search=TelemetrSearchResult(status="ok")),
    )
    campaign_id = await _new_campaign()

    stage = await run_search(campaign_id, LISTENER_ID, _request())

    assert stage.replaced is False
    assert stage.error == "unexpected_result"


@pytest.mark.asyncio
async def test_a_flood_wait_stops_the_keyword_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every further read lands inside the live window, and Telegram escalates repeats."""
    reader = ReadRecorder(search=read_error("FloodWait(1800s)"), similar=matches(("x", "X", None)))
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await _new_campaign()

    stage = await run_search(
        campaign_id,
        LISTENER_ID,
        _request(keywords=["alpha", "bravo", "charlie"], seed_channel="@durov"),
    )

    # One attempt, then stop — not one per keyword, and the seed pass is skipped too.
    assert len(reader.search_actions()) == 1
    assert reader.actions_of("get_similar_channels") == []
    assert stage.flooded is True


@pytest.mark.asyncio
async def test_a_flood_wait_still_queries_the_catalogue(monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTP to a third party spends no Telegram flood budget, and the slot is already spent.

    Breaking out of the keyword loop cancelled every remaining catalogue query too, so a
    single FloodWait removed the only filter-aware source from the run.
    """
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=read_error("FloodWait(600s)")))
    telemetr = TelemetrRecorder(telemetr_ok(("fromcatalogue", "C", 900)))
    monkeypatch.setattr(_seams, "search_telemetr", telemetr)
    campaign_id = await _new_campaign()

    stage = await run_search(
        campaign_id,
        LISTENER_ID,
        _request(keywords=["alpha", "bravo"], use_telemetr=True),
    )

    assert [request.term for request in telemetr.requests] == ["alpha", "bravo"]
    assert stage.found == 1


@pytest.mark.asyncio
async def test_a_flood_wait_puts_the_account_on_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Discovery read both fleet flood signals but wrote neither, so its own was invisible."""
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=read_error("FloodWait(600s)")))
    campaign_id = await _new_campaign()

    await run_search(campaign_id, LISTENER_ID, _request())

    assert in_cooldown(LISTENER_ID, datetime.now(UTC)) is True


@pytest.mark.asyncio
async def test_the_similar_pass_outranks_the_catalogue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Priority, not arrival order: the similar pass runs last but still wins the tie."""
    monkeypatch.setattr(
        _seams,
        "execute_read",
        ReadRecorder(search=matches(), similar=matches(("LookAlike", "Native", None))),
    )
    monkeypatch.setattr(
        _seams,
        "search_telemetr",
        TelemetrRecorder(telemetr_ok(("lookalike", "Catalogue", 900))),
    )
    campaign_id = await _new_campaign()

    await run_search(
        campaign_id,
        LISTENER_ID,
        _request(use_telemetr=True, seed_channel="@durov"),
    )

    rows = (await list_discovery_candidates(campaign_id)).rows
    # Telegram's spelling and source label, with the count borrowed from the catalogue.
    assert [row.channel for row in rows] == ["LookAlike"]
    assert rows[0].source == "telegram_similar"
    assert rows[0].subscribers == 900


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
    """Native outranks Telemetr but carries no count, so counts must pool before dedup.

    Otherwise the preferred spelling shadows the only count anyone knew, and the
    channel slips through a filter it plainly fails.
    """
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches(("small", "S", None))))
    monkeypatch.setattr(
        _seams,
        "search_telemetr",
        TelemetrRecorder(telemetr_ok(("small", "S", 42))),
    )
    campaign_id = await _new_campaign()

    await run_search(
        campaign_id,
        LISTENER_ID,
        _request(use_telemetr=True, members_min=10_000),
    )

    assert (await list_discovery_candidates(campaign_id)).rows == []


@pytest.mark.asyncio
async def test_a_pooled_count_is_stored_on_the_surviving_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches(("big", "B", None))))
    monkeypatch.setattr(
        _seams,
        "search_telemetr",
        TelemetrRecorder(telemetr_ok(("big", "B", 50_000))),
    )
    campaign_id = await _new_campaign()

    await run_search(campaign_id, LISTENER_ID, _request(use_telemetr=True))

    rows = (await list_discovery_candidates(campaign_id)).rows
    # Native still wins the spelling and the source label; only the count is borrowed.
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
async def test_pacing_sleeps_between_keywords_only(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []

    async def _record(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(_seams, "sleep", _record)
    monkeypatch.setattr(settings.neurocomment, "discovery_qualify_delay_min_seconds", 1.5)
    monkeypatch.setattr(settings.neurocomment, "discovery_qualify_delay_max_seconds", 1.5)
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches()))
    campaign_id = await _new_campaign()

    await run_search(campaign_id, LISTENER_ID, _request(keywords=["alpha", "beta", "gamma"]))

    # Three keywords → two gaps; the first call is not preceded by a pause.
    assert slept == [1.5, 1.5]


@pytest.mark.asyncio
async def test_the_catalogue_key_is_read_once_per_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """It was read per keyword: ten DB reads with secret decryption for one static key."""
    from services.neurocomment import _discovery_search  # noqa: PLC0415

    reads: list[int] = []
    original = _discovery_search.load_warming_settings

    async def _count() -> object:
        reads.append(1)
        return await original()

    monkeypatch.setattr(_discovery_search, "load_warming_settings", _count)
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches()))
    monkeypatch.setattr(_seams, "search_telemetr", TelemetrRecorder(telemetr_ok()))
    campaign_id = await _new_campaign()

    await run_search(
        campaign_id,
        LISTENER_ID,
        _request(keywords=["alpha", "beta", "gamma"], use_telemetr=True),
    )

    assert reads == [1]
