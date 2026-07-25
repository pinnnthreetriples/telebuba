"""Discovery stage 1 — source fan-out, merge/dedup, and start-refusal statuses."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from core.config import settings
from core.db import create_account, create_campaign, upsert_warming_state
from core.repositories.neurocomment import list_discovery_candidates
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate
from schemas.neurocomment_discovery import DiscoverySearchRequest
from schemas.telemetr import TelemetrSearchResult
from schemas.warming import WarmingStateWrite
from services.neurocomment import _discovery_state, _seams
from services.neurocomment._discovery_search import run_search
from services.neurocomment._state import in_cooldown
from tests.services.neurocomment.discovery_support import (
    LISTENER_ID,
    ReadRecorder,
    TelemetrRecorder,
    drain_discovery,
    matches,
    read_error,
    seed_listener,
    start_run,
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


@pytest.mark.asyncio
async def test_native_search_runs_once_per_keyword(monkeypatch: pytest.MonkeyPatch) -> None:
    reader = ReadRecorder(search=matches(("alpha", "Alpha", 100)))
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await _new_campaign()

    found, error, _ = await run_search(
        campaign_id,
        LISTENER_ID,
        _request(keywords=["crypto", "trading"]),
    )

    assert error is None
    assert found == 1  # both keywords returned the same channel -> deduped
    queries = [action.query for action in reader.search_actions()]
    assert queries == ["crypto", "trading"]


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

    found, _, _ = await run_search(campaign_id, LISTENER_ID, _request(seed_channel="@durov"))

    assert found == 2
    assert [action.seed for action in reader.similar_actions()] == ["@durov"]


@pytest.mark.asyncio
async def test_no_seed_means_no_recommendations_call(monkeypatch: pytest.MonkeyPatch) -> None:
    reader = ReadRecorder(search=matches(("alpha", "A", None)))
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await _new_campaign()

    await run_search(campaign_id, LISTENER_ID, _request())

    assert reader.actions_of("get_similar_channels") == []


@pytest.mark.asyncio
async def test_native_wins_a_cross_source_tie(monkeypatch: pytest.MonkeyPatch) -> None:
    """Telegram's spelling is canonical — adopt writes it into the campaign verbatim."""
    reader = ReadRecorder(search=matches(("CryptoNews", "Native", 500)))
    telemetr = TelemetrRecorder(telemetr_ok(("cryptonews", "Catalogue", 999)))
    monkeypatch.setattr(_seams, "execute_read", reader)
    monkeypatch.setattr(_seams, "search_telemetr", telemetr)
    campaign_id = await _new_campaign()

    await run_search(campaign_id, LISTENER_ID, _request(use_telemetr=True))

    rows = (await list_discovery_candidates(campaign_id)).rows
    assert [row.channel for row in rows] == ["CryptoNews"]
    assert rows[0].source == "telegram_search"
    assert rows[0].title == "Native"


@pytest.mark.asyncio
async def test_telemetr_is_not_called_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    telemetr = TelemetrRecorder(telemetr_ok(("never", "N", None)))
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches()))
    monkeypatch.setattr(_seams, "search_telemetr", telemetr)
    campaign_id = await _new_campaign()

    await run_search(campaign_id, LISTENER_ID, _request(use_telemetr=False))

    assert telemetr.requests == []


@pytest.mark.asyncio
async def test_telemetr_filters_ride_the_request(monkeypatch: pytest.MonkeyPatch) -> None:
    telemetr = TelemetrRecorder(telemetr_ok())
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches()))
    monkeypatch.setattr(_seams, "search_telemetr", telemetr)
    campaign_id = await _new_campaign()

    await run_search(
        campaign_id,
        LISTENER_ID,
        _request(use_telemetr=True, country="ae", language="ar", members_min=100),
    )

    sent = telemetr.requests[0]
    assert sent.term == "crypto"
    assert sent.country == "ae"
    assert sent.language == "ar"
    assert sent.members_min == 100


@pytest.mark.asyncio
async def test_missing_telemetr_key_skips_the_source_without_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unconfigured catalogue is a skipped source, not a degraded run."""
    telemetr = TelemetrRecorder(TelemetrSearchResult(status="not_configured"))
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches(("alpha1", "A", None))))
    monkeypatch.setattr(_seams, "search_telemetr", telemetr)
    campaign_id = await _new_campaign()

    found, error, _ = await run_search(campaign_id, LISTENER_ID, _request(use_telemetr=True))

    assert error is None
    assert found == 1


@pytest.mark.asyncio
async def test_telemetr_rate_limit_keeps_native_results_and_reports_the_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telemetr = TelemetrRecorder(TelemetrSearchResult(status="rate_limited", error="HTTP 429"))
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches(("native", "N", 10))))
    monkeypatch.setattr(_seams, "search_telemetr", telemetr)
    campaign_id = await _new_campaign()

    found, error, _ = await run_search(campaign_id, LISTENER_ID, _request(use_telemetr=True))

    assert found == 1
    assert error == "telemetr_rate_limited"


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

    found, error, _ = await run_search(campaign_id, LISTENER_ID, _request(seed_channel="seed"))

    assert found == 1
    assert error == "RPC: ChannelPrivateError"


@pytest.mark.asyncio
async def test_a_flood_wait_stops_the_keyword_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every further read lands inside the live window, and Telegram escalates repeats."""
    reader = ReadRecorder(search=read_error("FloodWait(1800s)"), similar=matches(("x", "X", None)))
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await _new_campaign()

    await run_search(
        campaign_id,
        LISTENER_ID,
        _request(keywords=["alpha", "bravo", "charlie"], seed_channel="@durov"),
    )

    # One attempt, then stop — not one per keyword, and the seed pass is skipped too.
    assert len(reader.search_actions()) == 1
    assert reader.actions_of("get_similar_channels") == []


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
async def test_a_warming_account_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Warming's freeze avoidance assumes it owns its accounts' traffic."""
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches()))
    await seed_listener()
    campaign_id = await _new_campaign()
    await upsert_warming_state(WarmingStateWrite(account_id=LISTENER_ID, state="active"))

    refused = await start_run(campaign_id, _request())

    assert refused.status == "account_cooling"


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

    found, _, _ = await run_search(campaign_id, LISTENER_ID, _request())

    assert found == 2


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
async def test_start_discovery_spawns_and_reports_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches(("alpha1", "A", None))))
    await seed_listener()
    campaign_id = await _new_campaign()

    outcome = await start_run(campaign_id, _request())

    assert outcome.status == "started"


@pytest.mark.asyncio
async def test_start_discovery_is_single_flighted(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio  # noqa: PLC0415

    async def _slow(_account_id: str, _action: object) -> object:
        await asyncio.sleep(5)
        return matches()

    monkeypatch.setattr(_seams, "execute_read", _slow)
    await seed_listener()
    campaign_id = await _new_campaign()

    first = await start_run(campaign_id, _request())
    second = await start_run(campaign_id, _request())

    assert first.status == "started"
    assert second.status == "already_running"


@pytest.mark.asyncio
async def test_start_discovery_without_any_account_refuses() -> None:
    campaign_id = await _new_campaign()

    outcome = await start_run(campaign_id, _request())

    assert outcome.status == "no_account"


@pytest.mark.asyncio
async def test_start_discovery_falls_back_to_a_campaign_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.db import assign_account_to_campaign  # noqa: PLC0415

    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches()))
    await create_account(
        AccountCreate(account_id="acc-serving", label="server", session_name="acc-serving")
    )
    campaign_id = await _new_campaign()
    await assign_account_to_campaign(campaign_id, "acc-serving")

    outcome = await start_run(campaign_id, _request())

    assert outcome.status == "started"


@pytest.mark.asyncio
async def test_start_discovery_refuses_a_cooling_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Searching on a rate-limited account would deepen the very limit it is serving."""
    from datetime import UTC, datetime, timedelta  # noqa: PLC0415

    from services.neurocomment import _state  # noqa: PLC0415

    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches()))
    await seed_listener()
    campaign_id = await _new_campaign()
    await _state.set_cooldown(LISTENER_ID, datetime.now(UTC) + timedelta(hours=1))

    outcome = await start_run(campaign_id, _request())

    assert outcome.status == "account_cooling"


@pytest.mark.asyncio
async def test_start_discovery_refuses_an_account_in_warming_flood_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime, timedelta  # noqa: PLC0415

    from services.warming._state import _set_state  # noqa: PLC0415

    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches()))
    await seed_listener()
    campaign_id = await _new_campaign()
    until = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    await _set_state(LISTENER_ID, "flood_wait", flood_wait_until=until)

    outcome = await start_run(campaign_id, _request())

    assert outcome.status == "account_cooling"


@pytest.mark.asyncio
async def test_start_discovery_honours_the_daily_search_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.neurocomment, "discovery_max_searches_per_day", 1)
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches()))
    await seed_listener()
    first_campaign = await _new_campaign()
    second_campaign = await _new_campaign()

    first = await start_run(first_campaign, _request())
    # Let it finish first: both campaigns resolve to the same listener, so an
    # overlapping start is refused for holding the account before the cap is consulted.
    await drain_discovery(first_campaign)
    second = await start_run(second_campaign, _request())

    assert first.status == "started"
    assert second.status == "daily_limit_reached"


@pytest.mark.asyncio
async def test_daily_cap_does_not_count_a_refused_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refusal must not consume the operator's allowance."""
    monkeypatch.setattr(settings.neurocomment, "discovery_max_searches_per_day", 1)
    campaign_id = await _new_campaign()

    refused = await start_run(campaign_id, _request())

    assert refused.status == "no_account"
    assert _discovery_state.at_daily_search_cap() is False


@pytest.mark.asyncio
async def test_listener_is_preferred_over_a_campaign_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Discovery traffic must stay off the commenting accounts."""
    from core.db import assign_account_to_campaign  # noqa: PLC0415

    seen: list[str] = []

    async def _record(account_id: str, _action: object) -> object:
        seen.append(account_id)
        return matches()

    monkeypatch.setattr(_seams, "execute_read", _record)
    await create_account(
        AccountCreate(account_id="acc-serving", label="server", session_name="acc-serving")
    )
    campaign_id = await _new_campaign()
    await assign_account_to_campaign(campaign_id, "acc-serving")
    await seed_listener()

    # Through start_discovery, not run_search: handing the account in as a literal
    # would assert nothing about which one the policy actually picks.
    await start_run(campaign_id, _request())
    await drain_discovery(campaign_id)

    assert seen == [LISTENER_ID]
