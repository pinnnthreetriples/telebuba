"""A re-join stamp nobody was answering is not a spent budget — #48's general answer.

``_channel_pause.review_expired_pauses`` refuses to read a verdict off a pause deadline that
ran out more than a window ago: a stopped campaign, a long shutdown, or a budget lowered under
the row leaves a deadline lying there, and nobody was posting against it. Migration #48's
docstring calls that freshness check the general answer for any LATER change to these
settings — and for the re-join rule it was simply not true. Nothing here looked at the age of
``rejoin_attempted_at``, so one tick of ``review_access_lost`` executed a channel off a row
days past its window, without a single attempt under the new budget.

Own module because ``test_rejoin`` is at the 700-line test cap.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.config import settings
from core.db import (  # type: ignore[attr-defined]
    _get_engine,
    assign_account_to_campaign,
    create_account,
    create_campaign,
    fetch_readiness,
    link_channel_to_campaign,
    list_campaign_channels,
    list_recent_logs,
    upsert_readiness,
)
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate
from services.neurocomment import _rejoin, _runtime

pytestmark = pytest.mark.usefixtures("isolate_onboarding")

_CHANNEL = "@chan"


async def _campaign(account_id: str) -> str:
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    await link_channel_to_campaign(campaign.campaign_id, _CHANNEL)
    await create_account(AccountCreate(account_id=account_id, session_name=account_id))
    await assign_account_to_campaign(campaign.campaign_id, account_id)
    return campaign.campaign_id


async def _park_with_an_old_stamp(account_id: str, *, attempts: int, hours: float) -> None:
    """Park the pair with ``attempts`` spent and its last stamp ``hours`` in the past.

    Written straight onto the row rather than stamped: ``stamp_rejoin_attempt`` writes the wall
    clock, and this rule's whole question is how old the stamp is.
    """
    await upsert_readiness(account_id, _CHANNEL, joined=False, captcha_passed=True, ready=False)
    stamp = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    with _get_engine().begin() as connection:
        connection.exec_driver_sql(
            "UPDATE neurocomment_readiness SET rejoin_attempts = ?, rejoin_attempted_at = ? "
            "WHERE account_id = ? AND channel = ?",
            (attempts, stamp, account_id, _CHANNEL),
        )


@pytest.mark.asyncio
async def test_a_budget_shrunk_under_a_stale_stamp_costs_an_attempt_not_the_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The operator lowers ``channel_max_rounds`` and the next tick must not execute anyone.

    A pair one attempt into the old budget is instantly ``exhausted`` under the new one, and
    its window ran out long ago, so the give-up test was true on the first tick: the channel
    left the campaign without one re-join on the budget the operator had just set. The stamp is
    two windows old — nobody was re-joining across it, which is exactly what the sibling rule
    calls a lying row — so the pair gets an attempt instead of a verdict.
    """
    monkeypatch.setattr(settings.neurocomment, "channel_max_rounds", 1)
    triggered: list[object] = []
    monkeypatch.setattr(_runtime, "_ensure_onboarding_running", triggered.append)
    campaign_id = await _campaign("acc-1")
    await _park_with_an_old_stamp("acc-1", attempts=1, hours=49)

    await _rejoin.review_access_lost(datetime.now(UTC))

    links = (await list_campaign_channels(campaign_id)).links
    assert [link.active for link in links] == [True]
    row = await fetch_readiness("acc-1", _CHANNEL)
    assert row is not None
    assert row.rejoin_attempts == 2  # an attempt on the NEW budget, not a verdict from the old
    assert len(triggered) == 1
    # ...and the operator never reads "2/1": the label is clamped, the way the pause rule
    # clamps a round counter its own release let outrun the budget.
    lines = [
        entry
        for entry in await list_recent_logs(limit=50)
        if entry.event == "neurocomment_rejoin_attempt"
    ]
    assert [line.extra["reason"] for line in lines] == ["1/1"]
