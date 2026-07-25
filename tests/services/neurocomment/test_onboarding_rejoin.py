"""Onboarding re-join test for the post-time access-loss park (#279).

``_generate._classify_post`` parks a pair that lost access with onboarding's
hard-join-failure sentinel ``(joined=False, captcha_passed=True, ready=False)``. The
engine-side half (the pair stops being selected) is covered in ``test_engine_outcomes``;
the load-bearing other half is that a later onboarding pass actually RE-JOINS it. Nothing
pinned that, so parking the pair as ``banned`` / ``human_skipped`` instead, or widening
the ``already_ready`` skip to cover the sentinel row, would strand it forever with a green
suite. Own module so ``test_onboarding_campaign`` stays under the 700-line test cap.
"""

from __future__ import annotations

import pytest

from core.db import (
    assign_account_to_campaign,
    create_account,
    create_campaign,
    fetch_readiness,
    link_channel_to_campaign,
    mark_human_skipped,
    mark_pair_banned,
    upsert_readiness,
)
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate
from services import neurocomment
from services.neurocomment import _seams, onboarding
from tests.services.neurocomment.onboarding_support import (
    _JoinStub,
    _no_sleep,
    _ReadStub,
)

pytestmark = pytest.mark.usefixtures("isolate_onboarding")


@pytest.mark.asyncio
async def test_access_lost_pair_is_rejoined_while_banned_and_skipped_are_left_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One pass, three parked pairs: only the access-loss sentinel spends a join RPC.

    The three readiness rows the post path can leave behind, side by side, so the contrast
    is what the assertion pins: the sentinel must be re-joined back to ``ready``, while the
    sticky ban (#30) and the operator skip (#148) must cost zero join RPCs. A future change
    that parked lost access as either of those would flip the join count and fail here.
    """
    campaign = await create_campaign(CampaignCreate(name="Promo", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    for account_id in ("acc-park", "acc-ban", "acc-skip"):
        await create_account(
            AccountCreate(account_id=account_id, label=account_id, session_name=account_id),
        )
        await assign_account_to_campaign(campaign.campaign_id, account_id)
    # The #279 sentinel, exactly as ``_classify_post`` writes it on ChannelPrivateError.
    await upsert_readiness("acc-park", "@chan", joined=False, captcha_passed=True, ready=False)
    # Both of these were onboarded and then parked by a post-time outcome that must stick.
    await upsert_readiness("acc-ban", "@chan", joined=True, captcha_passed=True, ready=True)
    await mark_pair_banned("acc-ban", "@chan")
    await upsert_readiness("acc-skip", "@chan", joined=True, captcha_passed=True, ready=True)
    await mark_human_skipped("acc-skip", "@chan")

    read = _ReadStub(linked_chat_id=4423, comments_enabled=True)
    join = _JoinStub()
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)
    monkeypatch.setattr(onboarding.asyncio, "sleep", _no_sleep([]))

    result = await neurocomment.onboard_campaign(campaign.campaign_id)

    # Exactly one join RPC, and it belongs to the parked pair — recovery is real, not a
    # comment. The ban and the skip are left alone (a re-join would revive them).
    assert [account_id for account_id, _ in join.calls] == ["acc-park"]
    assert {(o.account_id, o.state) for o in result.outcomes} == {
        ("acc-park", "ready"),
        ("acc-ban", "banned"),
        ("acc-skip", "human_skipped"),
    }
    rejoined = await fetch_readiness("acc-park", "@chan")
    assert rejoined is not None
    assert (rejoined.joined, rejoined.captcha_passed, rejoined.ready) == (True, True, True)
