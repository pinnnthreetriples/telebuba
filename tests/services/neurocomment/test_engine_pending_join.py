"""A pending approval request parks ONE pair, never the account.

Readiness is per (account, channel), but nothing pinned that the join-request state
stays that way — an account waiting on an admin in a gated group has to keep
commenting in the open channels it is already a member of, or one unapproved channel
quietly costs the campaign its whole fleet.
"""

from __future__ import annotations

import pytest

from core.db import (
    fetch_comment,
    link_channel_to_campaign,
    stamp_join_request,
    upsert_readiness,
)
from schemas.telegram_actions import NewPostEvent
from services.neurocomment import engine
from tests.services.neurocomment.engine_support import (
    _CommentStub,
    _make_campaign,
    _patch_io,
)

pytestmark = pytest.mark.usefixtures("isolate_engine")


@pytest.mark.asyncio
async def test_pending_join_request_does_not_block_the_open_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign_id = await _make_campaign("@open", "acc-1")
    await link_channel_to_campaign(campaign_id, "@gated")
    # acc-1 is stuck awaiting approval on @gated: not joined, not ready, request out.
    await upsert_readiness("acc-1", "@gated", joined=False, captcha_passed=False, ready=False)
    await stamp_join_request("acc-1", "@gated")
    comment = _CommentStub(status="ok")
    _patch_io(monkeypatch, comment=comment)

    await engine.handle_new_post(NewPostEvent(channel="@open", post_id=10, text="hi there"))

    assert [account_id for account_id, _action in comment.calls] == ["acc-1"]
    posted = await fetch_comment("@open", 10)
    assert posted is not None
    assert posted.status == "posted"
