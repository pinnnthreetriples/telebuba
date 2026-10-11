"""The run lifecycle: start refusals, pause and continue, a restart, ownership."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core.repositories import masstagging as repository
from schemas.masstagging_records import TargetSeed
from services import _account_owner
from services import masstagging as service
from services.masstagging import _runtime
from tests.services.masstagging.fakes import account, run_to_end, seed

if TYPE_CHECKING:
    from tests.services.masstagging.fakes import FakeTelegram


async def _status(campaign_id: str) -> str:
    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    return record.run_status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"accounts": ()}, "masstagging_no_accounts"),
        ({"targets": ()}, "masstagging_no_targets"),
        ({"media": []}, "masstagging_no_media"),
    ],
)
async def test_start_refuses_what_lacks_something(
    telegram: FakeTelegram, overrides: dict[str, object], code: str
) -> None:
    campaign_id = await seed(**overrides)  # ty: ignore[invalid-argument-type]

    with pytest.raises(service.MasstaggingConflictError, match=code):
        await service.start_campaign(campaign_id)
    assert telegram.stories == []


@pytest.mark.asyncio
async def test_start_refuses_a_stale_version_and_a_second_start(telegram: FakeTelegram) -> None:
    campaign_id = await seed()
    with pytest.raises(service.MasstaggingConflictError, match="campaign_changed"):
        await service.start_campaign(campaign_id, expected_updated_at="2020-01-01T00:00:00+00:00")
    telegram.hold = asyncio.Event()
    assert await service.start_campaign(campaign_id)
    with pytest.raises(service.MasstaggingConflictError, match="campaign_running"):
        await service.start_campaign(campaign_id)
    await service.stop_campaign(campaign_id)


@pytest.mark.asyncio
async def test_an_account_another_feature_drives_is_left_out(telegram: FakeTelegram) -> None:
    campaign_id = await seed(accounts=("a1", "a2"), mode="priming", media=[])
    _account_owner.try_claim("a1", "chat_broadcast", "other")

    await run_to_end(campaign_id)

    assert {account_id for account_id, _peer in telegram.toggles} == {"a2"}


@pytest.mark.asyncio
async def test_start_refuses_when_no_account_is_free(telegram: FakeTelegram) -> None:
    campaign_id = await seed()
    _account_owner.try_claim("a1", "user_parser", "run")

    with pytest.raises(service.MasstaggingConflictError, match="masstagging_no_free_accounts"):
        await service.start_campaign(campaign_id)
    assert telegram.stories == []


@pytest.mark.asyncio
async def test_a_spam_blocked_or_dead_account_never_works(telegram: FakeTelegram) -> None:
    from core.repositories.spam_status import upsert_spam_status  # noqa: PLC0415
    from schemas.spam_status import SpamStatusVerdict  # noqa: PLC0415

    campaign_id = await seed(accounts=("a1", "a2", "a3"), mode="priming", media=[])
    await upsert_spam_status(
        SpamStatusVerdict(account_id="a1", status="limited", checked_at="2026-10-11T00:00:00")
    )
    from core.repositories.accounts import update_account_status  # noqa: PLC0415

    await update_account_status("a2", status="unauthorized")

    await run_to_end(campaign_id)

    assert {account_id for account_id, _peer in telegram.toggles} == {"a3"}


@pytest.mark.asyncio
async def test_the_run_holds_its_accounts_and_lets_go_when_done(telegram: FakeTelegram) -> None:
    campaign_id = await seed()
    telegram.hold = asyncio.Event()
    assert await service.start_campaign(campaign_id)
    assert _account_owner.owner_of("a1") == "masstagging"

    telegram.hold.set()
    task = _runtime._TASKS[campaign_id]
    await task

    assert _account_owner.owner_of("a1") is None
    assert await _status(campaign_id) == "done"


@pytest.mark.asyncio
async def test_pause_puts_the_batch_back_and_continue_keeps_the_run(
    telegram: FakeTelegram,
) -> None:
    campaign_id = await seed(mode="priming", media=[], targets=("@alpha", "@beta"))
    telegram.hold = asyncio.Event()
    assert await service.start_campaign(campaign_id)
    await telegram.entered.wait()
    record = await repository.fetch_campaign(campaign_id)
    assert record is not None

    assert await service.stop_campaign(campaign_id)

    assert await _status(campaign_id) == "paused"
    states = {t.state for t in await repository.list_targets(campaign_id)}
    assert states == {"queued"}
    telegram.hold = None
    await run_to_end(campaign_id)
    resumed = await repository.fetch_campaign(campaign_id)
    assert resumed is not None
    assert (resumed.run_status, resumed.run_id) == ("done", record.run_id)
    assert len(telegram.toggles) == 2


@pytest.mark.asyncio
async def test_a_pause_while_a_story_is_in_flight_never_posts_it_again(
    telegram: FakeTelegram,
) -> None:
    campaign_id = await seed(targets=("@alpha",))
    telegram.hold = asyncio.Event()
    assert await service.start_campaign(campaign_id)
    await telegram.entered.wait()

    assert await service.stop_campaign(campaign_id)

    rows = {t.raw: (t.state, t.reason) for t in await repository.list_targets(campaign_id)}
    assert rows == {"@alpha": ("failed", "unconfirmed")}


@pytest.mark.asyncio
async def test_start_again_lays_the_list_out_afresh(telegram: FakeTelegram) -> None:
    campaign_id = await seed(targets=("@alpha",), skip_tagged=False)
    await run_to_end(campaign_id)
    first = await repository.fetch_campaign(campaign_id)

    await run_to_end(campaign_id)

    second = await repository.fetch_campaign(campaign_id)
    assert first is not None
    assert second is not None
    assert first.run_id != second.run_id
    assert telegram.tagged() == ["alpha", "alpha"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("telegram")
async def test_a_restart_resumes_and_never_reposts_a_story_in_flight() -> None:
    campaign_id = await seed(targets=("@alpha", "@beta", "@gamma"))
    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    await repository.set_status(campaign_id, "running", run_id="run-1", session_unix=0)
    seeds = [
        TargetSeed(target_key=f"u:{name}", raw=f"@{name}", kind="username")
        for name in ("alpha", "beta", "gamma")
    ]
    await repository.replace_targets(campaign_id, seeds, at=0)
    await repository.update_targets(
        campaign_id, ["u:alpha"], at=0, state="working", reason="unconfirmed", account_id="a1"
    )
    await repository.update_targets(campaign_id, ["u:beta"], at=0, state="working", account_id="a1")

    await service.reconcile_masstagging_on_startup()
    await _runtime._TASKS[campaign_id]

    rows = {t.raw: (t.state, t.reason) for t in await repository.list_targets(campaign_id)}
    assert rows["@alpha"] == ("failed", "unconfirmed")
    assert rows["@beta"] == ("tagged", None)
    assert rows["@gamma"] == ("tagged", None)
    resumed = await repository.fetch_campaign(campaign_id)
    assert resumed is not None
    assert (resumed.run_status, resumed.run_id) == ("done", "run-1")


@pytest.mark.asyncio
async def test_a_campaign_caught_stopping_at_boot_is_paused(telegram: FakeTelegram) -> None:
    campaign_id = await seed()
    await repository.set_status(campaign_id, "stopping", run_id="run-1")

    await service.reconcile_masstagging_on_startup()

    assert await _status(campaign_id) == "paused"
    assert telegram.stories == []


@pytest.mark.asyncio
async def test_a_restart_with_no_free_account_fails_the_run(telegram: FakeTelegram) -> None:
    campaign_id = await seed()
    await repository.set_status(campaign_id, "running", run_id="run-1")
    await account("x9")

    from core.repositories.accounts import update_account_status  # noqa: PLC0415

    await update_account_status("a1", status="unauthorized")
    await service.reconcile_masstagging_on_startup()

    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert (record.run_status, record.last_error) == ("failed", "no_accounts")
    assert telegram.stories == []


@pytest.mark.asyncio
async def test_shutdown_fences_the_run_and_keeps_it_running(telegram: FakeTelegram) -> None:
    campaign_id = await seed()
    telegram.hold = asyncio.Event()
    assert await service.start_campaign(campaign_id)

    await service.shutdown_masstagging_on_shutdown()

    assert await _status(campaign_id) == "running"
    assert _account_owner.owner_of("a1") is None
