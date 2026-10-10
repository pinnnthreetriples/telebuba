"""The board (rows, counters, accounts) and the manual actions on one person."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core.repositories import masstagging as repository
from core.repositories.spam_status import upsert_spam_status
from schemas.masstagging import MasstaggingTargetAction
from schemas.masstagging_records import TargetSeed
from schemas.spam_status import SpamStatusVerdict
from services import _account_owner
from services import masstagging as service
from tests.services.masstagging.fakes import answer, run_to_end, seed

if TYPE_CHECKING:
    from tests.services.masstagging.fakes import FakeTelegram


async def _laid_out(campaign_id: str, *names: str) -> None:
    seeds = [TargetSeed(target_key=f"u:{n}", raw=f"@{n}", kind="username") for n in names]
    await repository.replace_targets(campaign_id, seeds, at=100)


@pytest.mark.asyncio
async def test_a_campaign_never_started_shows_its_list_as_the_queue() -> None:
    campaign_id = await seed(targets=("@alpha", "@beta"))

    board = await service.load_board(campaign_id)

    assert board is not None
    assert [(row.target, row.state, row.active) for row in board.rows] == [
        ("@alpha", "queued", True),
        ("@beta", "queued", True),
    ]
    assert board.campaign.status == "ready"
    assert (board.counters.total, board.counters.left, board.counters.per_day) == (2, 2, 75)
    assert board.counters.days_left == 1


@pytest.mark.asyncio
async def test_a_finished_board_has_history_and_counters(telegram: FakeTelegram) -> None:
    campaign_id = await seed(mode="priming", media=[], targets=("@alpha", "@beta"))
    telegram.toggle_answers["a1"] = [answer("failed", error_type="UserPrivacyRestrictedError")]

    await run_to_end(campaign_id)
    board = await service.load_board(campaign_id)

    assert board is not None
    rows = {row.target: row for row in board.rows}
    assert (rows["@alpha"].state, rows["@alpha"].reason, rows["@alpha"].active) == (
        "failed",
        "privacy",
        False,
    )
    assert [entry.event for entry in rows["@beta"].history] == ["tagged"]
    assert rows["@beta"].account_id == "a1"
    counters = board.counters
    assert (counters.tagged, counters.failed, counters.left, counters.days_left) == (1, 1, 0, None)
    assert board.campaign.status == "done"


@pytest.mark.asyncio
async def test_the_board_names_excluded_busy_and_free_accounts() -> None:
    campaign_id = await seed(accounts=("a1", "a2", "a3"), premium=False)
    await upsert_spam_status(
        SpamStatusVerdict(account_id="a2", status="limited", checked_at="2026-10-11T00:00:00")
    )
    _account_owner.try_claim("a3", "neuroshilling", "c")

    board = await service.load_board(campaign_id)

    assert board is not None
    states = {a.account_id: (a.state, a.busy_owner) for a in board.accounts}
    assert states == {
        "a1": ("working", None),
        "a2": ("spamblock", None),
        "a3": ("busy", "neuroshilling"),
    }
    counters = board.counters
    assert (counters.working_accounts, counters.excluded_accounts, counters.free_accounts) == (
        1,
        1,
        1,
    )
    assert counters.per_day == 5


@pytest.mark.asyncio
async def test_skip_now_and_requeue() -> None:
    campaign_id = await seed()
    await _laid_out(campaign_id, "alpha", "beta")

    async def act(key: str, action: str) -> None:
        request = MasstaggingTargetAction(target_key=key, action=action)  # ty: ignore[invalid-argument-type]
        assert await service.act_on_target(campaign_id, request)

    await act("u:beta", "now")
    targets = await repository.list_targets(campaign_id)
    assert [t.raw for t in targets] == ["@beta", "@alpha"]

    await act("u:alpha", "skip")
    alpha = await repository.fetch_target(campaign_id, "u:alpha")
    assert alpha is not None
    assert (alpha.state, alpha.reason) == ("skipped", "manual")

    await act("u:alpha", "requeue")
    alpha = await repository.fetch_target(campaign_id, "u:alpha")
    assert alpha is not None
    assert (alpha.state, alpha.reason) == ("queued", None)
    events = [e.event for e in await repository.list_journal(campaign_id)]
    assert events == ["now", "skipped", "requeued"]


@pytest.mark.asyncio
async def test_an_action_on_a_moved_or_unknown_target_is_refused() -> None:
    campaign_id = await seed()
    await _laid_out(campaign_id, "alpha")

    with pytest.raises(service.MasstaggingConflictError, match="target_state_changed"):
        await service.act_on_target(
            campaign_id, MasstaggingTargetAction(target_key="u:alpha", action="requeue")
        )
    with pytest.raises(service.MasstaggingConflictError, match="masstagging_target_not_found"):
        await service.act_on_target(
            campaign_id, MasstaggingTargetAction(target_key="u:ghost", action="skip")
        )
    assert not await service.act_on_target(
        "nope", MasstaggingTargetAction(target_key="u:alpha", action="skip")
    )


@pytest.mark.asyncio
async def test_a_running_board_reports_running_and_its_accounts(
    telegram: FakeTelegram,
) -> None:
    campaign_id = await seed()
    telegram.hold = asyncio.Event()
    assert await service.start_campaign(campaign_id)
    await telegram.entered.wait()

    board = await service.load_board(campaign_id)

    assert board is not None
    assert board.campaign.status == "running"
    assert {row.state for row in board.rows} == {"working"}
    await service.stop_campaign(campaign_id)
    paused = await service.load_board(campaign_id)
    assert paused is not None
    assert paused.campaign.status == "paused"


@pytest.mark.asyncio
async def test_a_resting_campaign_shows_until_when() -> None:
    campaign_id = await seed()
    from services.masstagging import _seams  # noqa: PLC0415

    await repository.set_status(
        campaign_id, "running", run_id="r", rest_until_unix=_seams.now() + 60
    )

    board = await service.load_board(campaign_id)

    assert board is not None
    assert board.campaign.status == "resting"
    assert board.campaign.rest_until is not None
