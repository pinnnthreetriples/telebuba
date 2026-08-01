"""The listener's join cache survives a process restart (#40).

``_JOINED_CHANNELS`` is in-memory, so before the join log carried the channel every
restart re-sent ``JoinChannel`` for the whole watch set. Telegram answers "ok" (not
``already_participant``) for a public channel the account is already in, so each
no-op counted against the rolling-24h cap and starved the joins that mattered.
"""

from __future__ import annotations

import pytest

from core.db import (
    count_account_joins_since,
    create_campaign,
    link_channel_to_campaign,
)
from schemas.neurocomment import CampaignCreate
from services.neurocomment import _runtime
from tests.services.neurocomment.runtime_support import (
    _drain_joins,
    _ExecuteSpy,
    _ListenerSpy,
    _patch_execute,
    _patch_listener,
)

pytestmark = pytest.mark.usefixtures("isolate_runtime")


@pytest.mark.asyncio
async def test_restart_does_not_rejoin_channels_from_the_join_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    await link_channel_to_campaign(campaign.campaign_id, "@a")
    await link_channel_to_campaign(campaign.campaign_id, "@b")
    _patch_listener(monkeypatch, _ListenerSpy())
    exec_spy = _ExecuteSpy()
    _patch_execute(monkeypatch, exec_spy)

    await _runtime.reconcile_neurocomment_runtime("listener-1")
    await _drain_joins()
    assert exec_spy.joined == [("listener-1", "@a"), ("listener-1", "@b")]
    assert await count_account_joins_since("listener-1", "1970-01-01") == 2

    # Restart: the process-lifetime cache is gone, the join log is not.
    _runtime._JOINED_CHANNELS.clear()  # simulates a fresh process
    await _runtime.reconcile_neurocomment_runtime("listener-1")
    await _drain_joins()

    assert exec_spy.joined == [("listener-1", "@a"), ("listener-1", "@b")]
    assert await count_account_joins_since("listener-1", "1970-01-01") == 2
    await _runtime.shutdown_neurocomment_runtime("listener-1")
