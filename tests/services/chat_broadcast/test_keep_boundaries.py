"""«Писать всё равно» against the run's own state: what a keep may and may not decide on.

A chat has its own rounds, so there is no round boundary left for a keep to land on; what
it still depends on is the campaign's status (``test_finish_race`` covers the finish).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.repositories import chat_broadcast as repository
from schemas.chat_broadcast import ChatBroadcastTargetAction
from schemas.chat_broadcast_records import TargetSeed
from services import chat_broadcast as service
from services.chat_broadcast._errors import ChatBroadcastConflictError
from tests.services.chat_broadcast.fakes import seed

if TYPE_CHECKING:
    from schemas.chat_broadcast import ChatBroadcastStatus
    from schemas.chat_broadcast_records import CampaignRecord, TargetRecord


def _keep(chat_key: str = "alpha") -> ChatBroadcastTargetAction:
    return ChatBroadcastTargetAction(chat_key=chat_key, action="keep")


def _seed(key: str) -> TargetSeed:
    return TargetSeed(
        chat_key=key,
        raw=f"@{key}",
        kind="public",
        username=key,
        assigned_account_id="a1",
        member_account_id="a1",
    )


async def _target(campaign_id: str, chat_key: str = "alpha") -> TargetRecord:
    target = await repository.fetch_target(campaign_id, chat_key)
    assert target is not None
    return target


async def _skipped_for_deletions(status: ChatBroadcastStatus) -> str:
    campaign_id = await seed(accounts=("a1",))
    await repository.replace_targets(campaign_id, [_seed("alpha"), _seed("beta")])
    await repository.set_status(campaign_id, status, run_id="run-1", round=2)
    await repository.update_target(
        campaign_id, "alpha", state="skipped", skip_reason="deleted", round=2
    )
    await repository.update_target(campaign_id, "beta", round=2)
    return campaign_id


@pytest.mark.asyncio
async def test_a_keep_that_keeps_losing_the_race_with_the_campaign_gives_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign_id = await _skipped_for_deletions("running")
    attempts = 0

    async def lost(*_args: object, **_fields: object) -> bool:
        nonlocal attempts
        attempts += 1
        return False

    monkeypatch.setattr(repository, "keep_target", lost)

    with pytest.raises(ChatBroadcastConflictError, match="target_state_changed"):
        await service.act_on_target(campaign_id, _keep())

    # It decided again from a fresh read each time, a few times, and then said so.
    assert attempts == 3
    target = await _target(campaign_id)
    assert (target.state, target.ignore_deleted) == ("skipped", False)


@pytest.mark.asyncio
async def test_a_keep_decides_again_when_the_status_moved_under_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign_id = await _skipped_for_deletions("running")
    real = repository.keep_target

    async def stopped_first(seen: CampaignRecord, chat_key: str, **fields: object) -> bool:
        # The operator's Stop lands between the read and the write.
        await repository.set_status(campaign_id, "stopped")
        return await real(seen, chat_key, **fields)

    monkeypatch.setattr(repository, "keep_target", stopped_first)

    await service.act_on_target(campaign_id, _keep())

    # Decided anew on "stopped": queued with no due time, for Continue to play.
    target = await _target(campaign_id)
    assert (target.state, target.round, target.next_action_unix) == ("queued", 2, None)
    assert target.ignore_deleted


@pytest.mark.asyncio
async def test_a_keep_is_refused_for_an_unknown_chat_and_one_not_skipped_for_deletions() -> None:
    campaign_id = await _skipped_for_deletions("running")
    await repository.update_target(campaign_id, "alpha", skip_reason="banned")

    with pytest.raises(ChatBroadcastConflictError, match="target_state_changed"):
        await service.act_on_target(campaign_id, _keep())
    with pytest.raises(ChatBroadcastConflictError, match="target_not_found"):
        await service.act_on_target(campaign_id, _keep("nope"))
    assert not await service.act_on_target("missing", _keep())
    assert not (await _target(campaign_id)).ignore_deleted
