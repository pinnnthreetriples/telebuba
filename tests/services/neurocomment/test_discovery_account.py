"""Which account a discovery run may use, and the start refusals that follow.

Split out of ``test_discovery_search.py`` (700-line test cap): that file covers the
source fan-out and the merge, this one covers ``resolve_search_account`` plus the
statuses ``start_discovery`` answers instead of raising.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.config import settings
from core.db import assign_account_to_campaign, create_account, upsert_warming_state
from core.repositories.neurocomment import set_listener_running
from schemas.accounts import AccountCreate
from schemas.warming import WarmingStateWrite
from services.neurocomment import _discovery_state, _seams, _state
from tests.services.neurocomment.discovery_support import (
    LISTENER_ID,
    ReadRecorder,
    drain_discovery,
    matches,
    new_campaign,
    search_request,
    seed_listener,
    start_run,
)

pytestmark = pytest.mark.usefixtures("isolate_discovery")


@pytest.mark.asyncio
async def test_start_discovery_spawns_and_reports_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches(("alpha1", "A", None))))
    await seed_listener()
    campaign_id = await new_campaign()

    outcome = await start_run(campaign_id, search_request())

    assert outcome.status == "started"


@pytest.mark.asyncio
async def test_start_discovery_is_single_flighted(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio  # noqa: PLC0415

    async def _slow(_account_id: str, _action: object) -> object:
        await asyncio.sleep(5)
        return matches()

    monkeypatch.setattr(_seams, "execute_read", _slow)
    await seed_listener()
    campaign_id = await new_campaign()

    first = await start_run(campaign_id, search_request())
    second = await start_run(campaign_id, search_request())

    assert first.status == "started"
    assert second.status == "already_running"


@pytest.mark.asyncio
async def test_start_discovery_without_any_account_refuses() -> None:
    campaign_id = await new_campaign()

    outcome = await start_run(campaign_id, search_request())

    assert outcome.status == "no_account"


@pytest.mark.asyncio
async def test_start_discovery_falls_back_to_a_campaign_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches()))
    await create_account(
        AccountCreate(account_id="acc-serving", label="server", session_name="acc-serving")
    )
    campaign_id = await new_campaign()
    await assign_account_to_campaign(campaign_id, "acc-serving")

    outcome = await start_run(campaign_id, search_request())

    assert outcome.status == "started"


@pytest.mark.asyncio
async def test_a_warming_account_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Warming's freeze avoidance assumes it owns its accounts' traffic."""
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches()))
    await seed_listener()
    campaign_id = await new_campaign()
    await upsert_warming_state(WarmingStateWrite(account_id=LISTENER_ID, state="active"))

    refused = await start_run(campaign_id, search_request())

    # busy, not cooling: the account is healthy, its session is just taken.
    assert refused.status == "account_busy"


@pytest.mark.asyncio
async def test_a_running_listener_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """The listener is preferred for being read-only, but a running one holds the session.

    Discovery would layer minutes of paced keyword reads plus up to 100 probes onto the
    account the live listener is reading with — the same mutual exclusion warming gets.
    """
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches()))
    await seed_listener()
    campaign_id = await new_campaign()
    await set_listener_running(running=True)

    refused = await start_run(campaign_id, search_request())

    assert refused.status == "account_busy"


@pytest.mark.asyncio
async def test_a_running_listener_does_not_block_a_serving_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard is about sharing one session, not about the listener existing."""
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches()))
    await create_account(
        AccountCreate(account_id="acc-serving", label="server", session_name="acc-serving")
    )
    campaign_id = await new_campaign()
    await assign_account_to_campaign(campaign_id, "acc-serving")
    await set_listener_running(running=True)

    outcome = await start_run(campaign_id, search_request())

    assert outcome.status == "started"


@pytest.mark.asyncio
async def test_start_discovery_refuses_a_cooling_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Searching on a rate-limited account would deepen the very limit it is serving."""
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches()))
    await seed_listener()
    campaign_id = await new_campaign()
    await _state.set_cooldown(LISTENER_ID, datetime.now(UTC) + timedelta(hours=1))

    outcome = await start_run(campaign_id, search_request())

    assert outcome.status == "account_cooling"


@pytest.mark.asyncio
async def test_start_discovery_refuses_an_account_in_warming_flood_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.warming._state import _set_state  # noqa: PLC0415

    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches()))
    await seed_listener()
    campaign_id = await new_campaign()
    until = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    await _set_state(LISTENER_ID, "flood_wait", flood_wait_until=until)

    outcome = await start_run(campaign_id, search_request())

    # ``flood_wait`` is an active warming state, so this account matches the busy
    # branch too — the Telegram-limit reason must win, which pins the check order.
    assert outcome.status == "account_cooling"


@pytest.mark.asyncio
async def test_start_discovery_honours_the_daily_search_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.neurocomment, "discovery_max_searches_per_day", 1)
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches()))
    await seed_listener()
    first_campaign = await new_campaign()
    second_campaign = await new_campaign()

    first = await start_run(first_campaign, search_request())
    # Let it finish first: both campaigns resolve to the same listener, so an
    # overlapping start is refused for holding the account before the cap is consulted.
    await drain_discovery(first_campaign)
    second = await start_run(second_campaign, search_request())

    assert first.status == "started"
    assert second.status == "daily_limit_reached"


@pytest.mark.asyncio
async def test_daily_cap_does_not_count_a_refused_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refusal must not consume the operator's allowance."""
    monkeypatch.setattr(settings.neurocomment, "discovery_max_searches_per_day", 1)
    campaign_id = await new_campaign()

    refused = await start_run(campaign_id, search_request())

    assert refused.status == "no_account"
    assert _discovery_state.at_daily_search_cap() is False


@pytest.mark.asyncio
async def test_listener_is_preferred_over_a_campaign_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Discovery traffic must stay off the commenting accounts."""
    seen: list[str] = []

    async def _record(account_id: str, _action: object) -> object:
        seen.append(account_id)
        return matches()

    monkeypatch.setattr(_seams, "execute_read", _record)
    await create_account(
        AccountCreate(account_id="acc-serving", label="server", session_name="acc-serving")
    )
    campaign_id = await new_campaign()
    await assign_account_to_campaign(campaign_id, "acc-serving")
    await seed_listener()

    # Through start_discovery, not run_search: handing the account in as a literal
    # would assert nothing about which one the policy actually picks.
    await start_run(campaign_id, search_request())
    await drain_discovery(campaign_id)

    assert seen == [LISTENER_ID]
