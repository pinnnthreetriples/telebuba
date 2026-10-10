"""Where the run writes: "where it already is", folders, and link resolution."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.repositories import chat_broadcast as repository
from schemas.chat_broadcast import ChatBroadcastResolveRequest
from schemas.telegram_actions_broadcast import ChatlistResult, WritableGroupsResult
from services import chat_broadcast as service
from tests.services.chat_broadcast.fakes import group, run_to_end, seed

if TYPE_CHECKING:
    from tests.services.chat_broadcast.fakes import FakeTelegram


@pytest.mark.asyncio
async def test_own_groups_shared_by_accounts_get_one_message(telegram: FakeTelegram) -> None:
    telegram.groups["a1"] = WritableGroupsResult(
        groups=[group(1, "Shared"), group(2, "Only a1")], channels_skipped=3
    )
    telegram.groups["a2"] = WritableGroupsResult(
        groups=[group(1, "Shared"), group(3, "Only a2"), group(4, "Excluded")],
        admin_only_skipped=1,
    )
    campaign_id = await seed(target_mode="own", targets=(), messages=("Hi",), own_excluded=["4"])

    await run_to_end(campaign_id)

    assert telegram.joins == []
    chats = sorted(s.chat for s in telegram.sent)
    assert chats == ["1", "2", "3"]
    targets = {t.chat_key: t for t in await repository.list_targets(campaign_id)}
    assert set(targets) == {"id:1", "id:2", "id:3"}
    # The shared group went to ONE account, and both accounts got work.
    assert {t.assigned_account_id for t in targets.values()} == {"a1", "a2"}


@pytest.mark.asyncio
async def test_own_chats_lists_groups_with_their_accounts(telegram: FakeTelegram) -> None:
    telegram.groups["a1"] = WritableGroupsResult(groups=[group(1, "Shared", "shared")])
    telegram.groups["a2"] = WritableGroupsResult(groups=[group(1, "Shared", "shared")])

    listed = await service.own_chats(["a1", "a2", "missing"])

    [shared] = listed.groups
    assert (shared.peer_id, shared.username, shared.account_ids) == ("1", "shared", ["a1", "a2"])


@pytest.mark.asyncio
async def test_a_folder_opens_into_its_groups_and_joins_each_one(telegram: FakeTelegram) -> None:
    telegram.folders["kq3Hf0dX"] = ChatlistResult(
        title="Crypto",
        groups=[group(5, "F5"), group(6, "F6", "alpha")],
        channels_skipped=2,
    )
    campaign_id = await seed(
        accounts=("a1",), targets=("@alpha", "t.me/addlist/kq3Hf0dX"), messages=("Hi",)
    )

    await run_to_end(campaign_id)

    keys = [t.chat_key for t in await repository.list_targets(campaign_id)]
    # The folder's group that is also @alpha is the same chat — written once.
    assert keys == ["alpha", "folder:kq3Hf0dX:5"]
    assert ("a1", "kq3Hf0dX:5") in telegram.joins
    assert len(telegram.sent) == 2


@pytest.mark.asyncio
@pytest.mark.usefixtures("telegram")
async def test_a_folder_that_cannot_be_opened_is_left_out() -> None:
    campaign_id = await seed(accounts=("a1",), targets=("t.me/addlist/gone",), messages=("Hi",))

    await run_to_end(campaign_id)

    assert await repository.list_targets(campaign_id) == []
    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert record.status == "done"


@pytest.mark.asyncio
async def test_resolve_classifies_links_and_counts_folders(telegram: FakeTelegram) -> None:
    telegram.folders["abc"] = ChatlistResult(title="F", groups=[group(1, "x"), group(2, "y")])

    resolved = await service.resolve(
        ChatBroadcastResolveRequest(
            targets=["@alpha t.me/+AbCdEfGh123", "t.me/addlist/abc", "t.me/addlist/zzz", "!!"],
            account_ids=["a1"],
        )
    )

    assert [(i.kind, i.key, i.error, i.folder_count) for i in resolved.items] == [
        ("public", "alpha", None, None),
        ("invite", "+AbCdEfGh123", None, None),
        ("folder", "addlist:abc", None, 2),
        ("folder", "addlist:zzz", "folder_unavailable", None),
        (None, None, "invalid_target", None),
    ]
    no_reader = await service.resolve(ChatBroadcastResolveRequest(targets=["t.me/addlist/abc"]))
    assert no_reader.items[0].error == "folder_unavailable"
