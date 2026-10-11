"""Repository behaviour of the masstagging domain: campaigns, roster, targets, days."""

from __future__ import annotations

import pytest

from core.db import create_account, delete_account
from core.repositories import masstagging as repository
from schemas.masstagging_records import JournalRecord, TargetSeed
from tests.factories import AccountCreateFactory


async def _campaign(account_ids: tuple[str, ...] = ("a1", "a2")) -> str:
    for account_id in account_ids:
        await create_account(AccountCreateFactory.build(account_id=account_id))
    created = await repository.create_campaign("Spring")
    saved = await repository.save_settings(
        created.campaign_id,
        name="Spring",
        mode="priming",
        settings_json='{"mode": "priming"}',
        account_ids=list(account_ids),
        expected_updated_at=created.updated_at,
    )
    assert saved is not None
    return created.campaign_id


def _seed(name: str, *, kind: str = "username") -> TargetSeed:
    return TargetSeed.model_validate({"target_key": f"u:{name}", "raw": f"@{name}", "kind": kind})


@pytest.mark.asyncio
async def test_create_save_and_counts() -> None:
    campaign_id = await _campaign()
    await repository.replace_targets(campaign_id, [_seed("x"), _seed("y")], at=10)

    [listed] = await repository.list_campaigns()

    assert listed.campaign_id == campaign_id
    assert (listed.run_status, listed.mode, listed.account_count, listed.target_count) == (
        "idle",
        "priming",
        2,
        2,
    )
    assert listed.settings_json == '{"mode": "priming"}'
    assert await repository.list_roster(campaign_id) == ["a1", "a2"]


@pytest.mark.asyncio
async def test_a_save_refuses_a_stale_stamp_and_a_live_run() -> None:
    campaign_id = await _campaign()
    current = await repository.fetch_campaign(campaign_id)
    assert current is not None

    stale = await repository.save_settings(
        campaign_id,
        name="n",
        mode="stories",
        settings_json="{}",
        account_ids=[],
        expected_updated_at="x",
    )
    assert stale is None
    assert await repository.set_status(campaign_id, "running", run_id="r1")
    running = await repository.fetch_campaign(campaign_id)
    assert running is not None
    assert running.updated_at > current.updated_at
    live = await repository.save_settings(
        campaign_id,
        name="n",
        mode="stories",
        settings_json="{}",
        account_ids=[],
        expected_updated_at=running.updated_at,
    )
    assert live is None
    assert await repository.list_running_account_names() == {
        "a1": (campaign_id, "Spring"),
        "a2": (campaign_id, "Spring"),
    }
    assert [r.campaign_id for r in await repository.list_live_campaigns()] == [campaign_id]


@pytest.mark.asyncio
async def test_the_pace_save_is_allowed_while_running_and_bumps_the_stamp() -> None:
    campaign_id = await _campaign()
    await repository.set_status(campaign_id, "running", run_id="r1")
    current = await repository.fetch_campaign(campaign_id)
    assert current is not None

    assert await repository.save_pace(
        campaign_id, settings_json="{}", expected_updated_at=current.updated_at
    )
    assert not await repository.save_pace(
        campaign_id, settings_json="{}", expected_updated_at=current.updated_at
    )


@pytest.mark.asyncio
async def test_state_moves_are_compare_and_set() -> None:
    campaign_id = await _campaign()
    await repository.replace_targets(campaign_id, [_seed("x"), _seed("y")], at=10)

    moved = await repository.update_targets(
        campaign_id, ["u:x", "u:y"], at=20, expect_states=("queued",), state="working"
    )
    again = await repository.update_target(
        campaign_id, "u:x", at=30, expect_states=("queued",), state="skipped"
    )

    assert sorted(moved) == ["u:x", "u:y"]
    assert again is False
    target = await repository.fetch_target(campaign_id, "u:x")
    assert target is not None
    assert (target.state, target.updated_unix) == ("working", 20)
    assert await repository.update_targets(campaign_id, [], at=40, state="queued") == []


@pytest.mark.asyncio
async def test_merge_keeps_progress_and_what_the_run_added() -> None:
    campaign_id = await _campaign()
    await repository.replace_targets(
        campaign_id, [_seed("done"), _seed("gone"), _seed("member", kind="link")], at=10
    )
    await repository.update_target(campaign_id, "u:done", at=11, state="tagged")

    await repository.merge_targets(campaign_id, [_seed("new")], at=12)

    keys = [t.target_key for t in await repository.list_targets(campaign_id)]
    assert keys == ["u:done", "u:member", "u:new"]
    assert await repository.add_targets(campaign_id, [_seed("new"), _seed("more")], at=13) == 1


@pytest.mark.asyncio
async def test_a_fresh_layout_drops_the_old_rows_and_history() -> None:
    campaign_id = await _campaign()
    await repository.replace_targets(campaign_id, [_seed("x")], at=10)
    await repository.add_events(
        campaign_id, [JournalRecord(target_key="u:x", event="tagged", at_unix=10)]
    )

    await repository.replace_targets(campaign_id, [_seed("y")], at=20)

    assert [t.raw for t in await repository.list_targets(campaign_id)] == ["@y"]
    assert await repository.list_journal(campaign_id) == []


@pytest.mark.asyncio
async def test_tagged_counts_per_account_since_a_moment() -> None:
    campaign_id = await _campaign()
    await repository.replace_targets(campaign_id, [_seed("x"), _seed("y"), _seed("z")], at=10)
    await repository.update_targets(campaign_id, ["u:x"], at=50, state="tagged", account_id="a1")
    await repository.update_targets(campaign_id, ["u:y"], at=150, state="tagged", account_id="a1")
    await repository.update_targets(campaign_id, ["u:z"], at=150, state="tagged", account_id="a2")

    assert await repository.count_tagged_since(campaign_id, "a1", 100) == 1


@pytest.mark.asyncio
async def test_the_registry_keeps_the_first_campaign() -> None:
    await repository.register_tagged([1, 2], campaign_id="c1", at=10)
    await repository.register_tagged([2, 3], campaign_id="c2", at=20)

    assert await repository.tagged_user_ids([1, 3, 4]) == {1, 3}
    assert await repository.tagged_user_ids([]) == set()


@pytest.mark.asyncio
async def test_day_counters_add_up_and_remember_the_first_day() -> None:
    await repository.bump_day("a1", "2026-10-10", stories=1, tags=5)
    await repository.bump_day("a1", "2026-10-11", tags=2)
    await repository.bump_day("a1", "2026-10-11", tags=1, resolves=3)
    await repository.set_day("a1", "2026-10-11", floods_in_row=2, paused_until_unix=99)

    days = await repository.fetch_days(["a1", "a2"], "2026-10-11")

    assert (days["a1"].tags, days["a1"].resolves, days["a1"].floods_in_row) == (3, 3, 2)
    assert days["a1"].paused_until_unix == 99
    assert days["a2"].tags == 0
    assert await repository.first_days(["a1", "a2"]) == {"a1": "2026-10-10"}
    assert await repository.first_days([]) == {}
    with pytest.raises(ValueError, match="Not a day counter"):
        await repository.bump_day("a1", "2026-10-11", floods_in_row=1)
    with pytest.raises(ValueError, match="Not a day field"):
        await repository.set_day("a1", "2026-10-11", tags=1)


@pytest.mark.asyncio
async def test_unknown_columns_are_refused() -> None:
    with pytest.raises(ValueError, match="Not a run-state column"):
        await repository.set_status("c", "running", name="x")
    with pytest.raises(ValueError, match="Not a runtime column"):
        await repository.set_runtime("c", run_id="x")


@pytest.mark.asyncio
async def test_deleting_an_account_purges_its_roster_and_days() -> None:
    campaign_id = await _campaign(("a1",))
    await repository.bump_day("a1", "2026-10-11", tags=1)

    await delete_account("a1")

    assert await repository.list_roster(campaign_id) == []
    assert await repository.first_days(["a1"]) == {}


@pytest.mark.asyncio
async def test_deleting_a_campaign_keeps_the_registry() -> None:
    campaign_id = await _campaign()
    await repository.register_tagged([7], campaign_id=campaign_id, at=1)

    assert await repository.delete_campaign(campaign_id)

    assert await repository.fetch_campaign(campaign_id) is None
    assert await repository.tagged_user_ids([7]) == {7}
