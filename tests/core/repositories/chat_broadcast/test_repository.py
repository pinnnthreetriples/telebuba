"""Repository behaviour of the chat-broadcast domain: campaigns, roster, chats, journal."""

from __future__ import annotations

import time

import pytest

from core.db import create_account
from core.repositories import chat_broadcast as repository
from schemas.accounts import AccountCreate
from schemas.chat_broadcast_records import JournalKey, TargetSeed


async def _accounts(*ids: str) -> None:
    for account_id in ids:
        await create_account(
            AccountCreate(account_id=account_id, label=account_id, session_name=account_id)
        )


async def _campaign(account_ids: tuple[str, ...] = ("a1", "a2")) -> str:
    await _accounts(*account_ids)
    created = await repository.create_campaign("Crypto")
    saved = await repository.save_settings(
        created.campaign_id,
        name="Crypto",
        settings_json="{}",
        account_ids=list(account_ids),
        expected_updated_at=created.updated_at,
    )
    assert saved is not None
    return created.campaign_id


def _seed(key: str, account: str | None = "a1") -> TargetSeed:
    return TargetSeed(chat_key=key, raw=f"@{key}", kind="public", assigned_account_id=account)


@pytest.mark.asyncio
async def test_create_list_and_counts() -> None:
    campaign_id = await _campaign()
    await repository.replace_targets(campaign_id, [_seed("x"), _seed("y")])

    [listed] = await repository.list_campaigns()

    assert listed.campaign_id == campaign_id
    assert (listed.status, listed.account_count, listed.target_count) == ("draft", 2, 2)


@pytest.mark.asyncio
async def test_save_settings_refuses_a_stale_stamp_and_a_live_run() -> None:
    campaign_id = await _campaign()
    current = await repository.fetch_campaign(campaign_id)
    assert current is not None

    stale = await repository.save_settings(
        campaign_id, name="n", settings_json="{}", account_ids=[], expected_updated_at="x"
    )
    assert stale is None
    assert await repository.set_status(campaign_id, "running", run_id="r1")
    running = await repository.fetch_campaign(campaign_id)
    assert running is not None
    live = await repository.save_settings(
        campaign_id,
        name="n",
        settings_json="{}",
        account_ids=[],
        expected_updated_at=running.updated_at,
    )
    assert live is None


@pytest.mark.asyncio
async def test_roster_replace_keeps_the_state_of_kept_accounts() -> None:
    campaign_id = await _campaign(("a1", "a2"))
    await _accounts("a3")
    await repository.halt_account(campaign_id, "a1", "peer_flood")
    await repository.set_consecutive_errors(campaign_id, "a1", 4)
    current = await repository.fetch_campaign(campaign_id)
    assert current is not None

    await repository.save_settings(
        campaign_id,
        name="Crypto",
        settings_json="{}",
        account_ids=["a3", "a1"],
        expected_updated_at=current.updated_at,
    )

    roster = await repository.list_roster(campaign_id)
    assert [(r.account_id, r.position, r.state) for r in roster] == [
        ("a3", 0, "active"),
        ("a1", 1, "halted"),
    ]
    assert roster[1].consecutive_errors == 4


@pytest.mark.asyncio
async def test_set_status_checks_the_stamp_and_rejects_unknown_columns() -> None:
    campaign_id = await _campaign()

    assert not await repository.set_status(campaign_id, "running", expected_updated_at="old")
    assert not await repository.set_status("missing", "running")
    with pytest.raises(ValueError, match="run-state"):
        await repository.set_status(campaign_id, "running", name="x")
    with pytest.raises(ValueError, match="runtime"):
        await repository.set_runtime(campaign_id, status="done")

    before = await repository.fetch_campaign(campaign_id)
    await repository.set_runtime(campaign_id, round=2, rest_until_unix=5)
    after = await repository.fetch_campaign(campaign_id)
    assert before is not None
    assert after is not None
    assert (after.round, after.rest_until_unix) == (2, 5)
    assert after.updated_at == before.updated_at


@pytest.mark.asyncio
async def test_live_campaigns_and_running_rosters() -> None:
    campaign_id = await _campaign()
    await repository.set_status(campaign_id, "running", run_id="r1")

    assert [c.campaign_id for c in await repository.list_live_campaigns()] == [campaign_id]
    names = await repository.list_running_account_names()
    assert names == {"a1": (campaign_id, "Crypto"), "a2": (campaign_id, "Crypto")}


@pytest.mark.asyncio
async def test_update_target_is_a_compare_and_set() -> None:
    campaign_id = await _campaign()
    await repository.replace_targets(campaign_id, [_seed("x")])

    assert await repository.update_target(campaign_id, "x", state="skipped", skip_reason="manual")
    moved = await repository.update_target(
        campaign_id, "x", expect_states=("queued",), state="writing"
    )

    target = await repository.fetch_target(campaign_id, "x")
    assert not moved
    assert target is not None
    assert (target.state, target.skip_reason) == ("skipped", "manual")
    assert await repository.fetch_target(campaign_id, "nope") is None


@pytest.mark.asyncio
async def test_merge_appends_new_chats_and_skips_removed_ones() -> None:
    campaign_id = await _campaign()
    await repository.replace_targets(campaign_id, [_seed("x"), _seed("y"), _seed("z")])
    await repository.update_target(campaign_id, "z", state="done")

    await repository.merge_targets(
        campaign_id, [_seed("x"), _seed("w")], round_number=2, removed={"y", "z"}
    )

    rows = {t.chat_key: t for t in await repository.list_targets(campaign_id)}
    assert [t.chat_key for t in await repository.list_targets(campaign_id)] == ["x", "y", "z", "w"]
    assert (rows["y"].state, rows["y"].skip_reason) == ("skipped", "removed")
    assert rows["z"].state == "done"
    assert (rows["w"].state, rows["w"].round) == ("queued", 2)


@pytest.mark.asyncio
async def test_round_transitions_requeue_finished_and_error_skipped_chats() -> None:
    campaign_id = await _campaign()
    await repository.replace_targets(
        campaign_id, [_seed("done"), _seed("err"), _seed("admin"), _seed("late")]
    )
    await repository.update_target(campaign_id, "done", state="round_done", step_index=2)
    await repository.update_target(campaign_id, "err", state="skipped", skip_reason="error")
    await repository.update_target(campaign_id, "admin", state="skipped", skip_reason="admin_only")

    assert await repository.start_round(campaign_id, 2) == 2
    await repository.update_target(campaign_id, "late", state="round_done")
    await repository.finish_rounds(campaign_id)

    rows = {t.chat_key: t for t in await repository.list_targets(campaign_id)}
    assert (rows["done"].state, rows["done"].round, rows["done"].step_index) == ("queued", 2, 0)
    assert (rows["err"].state, rows["err"].skip_reason) == ("queued", None)
    assert rows["admin"].state == "skipped"
    assert rows["late"].state == "done"


@pytest.mark.asyncio
async def test_settle_interrupted_restarts_half_done_steps() -> None:
    campaign_id = await _campaign()
    await repository.replace_targets(campaign_id, [_seed("j"), _seed("c"), _seed("r"), _seed("w")])
    await repository.update_target(campaign_id, "j", state="joining")
    await repository.update_target(campaign_id, "c", state="captcha")
    await repository.update_target(campaign_id, "r", state="reconnecting", next_action_unix=1)
    await repository.update_target(campaign_id, "w", state="writing", step_index=1)

    await repository.settle_interrupted(campaign_id)

    rows = {t.chat_key: t for t in await repository.list_targets(campaign_id)}
    assert (rows["j"].state, rows["c"].state) == ("queued", "queued")
    assert rows["r"].state == "reconnecting"
    assert (rows["r"].next_action_unix or 0) > 1
    assert (rows["w"].state, rows["w"].step_index) == ("writing", 1)


@pytest.mark.asyncio
async def test_journal_claims_once_and_unconfirms_what_a_crash_left() -> None:
    campaign_id = await _campaign()
    key = JournalKey(run_id="r1", chat_key="x", round=1, step_index=0)

    assert await repository.claim_message(
        key, campaign_id=campaign_id, account_id="a1", kind="text"
    )
    assert not await repository.claim_message(
        key, campaign_id=campaign_id, account_id="a2", kind="text"
    )
    second = key.model_copy(update={"step_index": 1})
    await repository.claim_message(second, campaign_id=campaign_id, account_id="a1", kind="photo")
    await repository.settle_message(key, "sent", text="hi", rewritten=True, tg_message_id=7)

    assert await repository.unconfirm_pending("r1") == 1
    rows = await repository.list_journal(campaign_id, "r1")
    assert [(r.step_index, r.status, r.text, r.rewritten) for r in rows] == [
        (0, "sent", "hi", True),
        (1, "unconfirmed", "", False),
    ]
    assert rows[0].sent_unix is not None


@pytest.mark.asyncio
async def test_journal_counts_and_written_before() -> None:
    campaign_id = await _campaign()
    old = JournalKey(run_id="old", chat_key="x", round=1, step_index=0)
    await repository.claim_message(
        old, campaign_id=campaign_id, account_id="a1", kind="text", peer_id=42
    )
    await repository.settle_message(old, "sent", tg_message_id=1)
    now = JournalKey(run_id="new", chat_key="y", round=1, step_index=0)
    await repository.claim_message(now, campaign_id=campaign_id, account_id="a1", kind="text")

    since = int(time.time()) - 60
    assert await repository.count_account_sends_since("a1", since) == 2
    assert await repository.count_account_sends_since("a2", since) == 0
    assert await repository.count_run_sent("new") == 1
    assert await repository.written_before("x", None, run_id="new")
    assert await repository.written_before("other", 42, run_id="new")
    assert not await repository.written_before("x", None, run_id="old")

    [sent] = await repository.list_journal(campaign_id, "old")
    await repository.mark_deleted([sent.id])
    await repository.mark_deleted([])
    [deleted] = await repository.list_journal(campaign_id, "old")
    assert deleted.status == "deleted"


@pytest.mark.asyncio
async def test_events_and_delete_cascade() -> None:
    campaign_id = await _campaign()
    await repository.replace_targets(campaign_id, [_seed("x")])
    await repository.add_event(
        campaign_id, "x", round_number=1, kind="handed", account_id="a2", detail="a1"
    )

    [event] = await repository.list_events(campaign_id)
    assert (event.kind, event.account_id, event.detail) == ("handed", "a2", "a1")

    assert await repository.delete_campaign(campaign_id)
    assert not await repository.delete_campaign(campaign_id)
    assert await repository.list_targets(campaign_id) == []
    assert await repository.list_events(campaign_id) == []
    assert await repository.list_roster(campaign_id) == []
