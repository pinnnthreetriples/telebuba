"""Re-linking a channel restarts the captcha rule's per-pair timeline too (#49).

``_link_channel_to_campaign`` calls a link "a fresh start for the channel, per-pair counters
included" and clears the join-request, re-join and unconfirmed-ban budgets on that promise.
The captcha rule (#49) owns a third per-pair budget — ``captcha_retry_at``, the one re-solve
it authorises, and ``captcha_gave_up``, the terminal verdict that ends the pair's stay — and
its channel drop prints the same "link it again" hint. These pin that the hint is true: a
re-linked channel's pairs can be onboarded again, and the pair that comes back is owed a
whole budget rather than one already reading as spent.

``banned`` (#30) and ``human_skipped`` (#148) are the deliberate exclusions and are pinned
here as such — the first is sticky by design, the second is the operator's own decision.
"""

from __future__ import annotations

import pytest

from core.db import (  # type: ignore[attr-defined]
    _get_engine,
    create_account,
    create_campaign,
    deactivate_channel,
    fetch_readiness,
    link_channel_to_campaign,
    mark_captcha_gave_up,
    mark_human_skipped,
    mark_pair_banned,
    stamp_captcha_retry,
    upsert_readiness,
)
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate

_CHANNEL = "@chan"
_ACCOUNT = "acc-1"


def _captcha_columns(account_id: str = _ACCOUNT, channel: str = _CHANNEL) -> tuple[str | None, int]:
    """The pair's raw ``(captcha_retry_at, captcha_gave_up)``.

    Read off the table like ``test_readiness_unconfirmed_ban._stamped_at`` does, so the
    assertion is about what the UPDATE wrote rather than what the model chose to expose.
    """
    with _get_engine().connect() as connection:
        row = connection.exec_driver_sql(
            "SELECT captcha_retry_at, captcha_gave_up FROM neurocomment_readiness "
            "WHERE account_id = ? AND channel = ?",
            (account_id, channel),
        ).first()
    assert row is not None
    return row[0], row[1]


async def _a_pair_that_gave_up_on_the_captcha() -> str:
    """A linked channel whose only pair spent its re-solve and was retired by the rule.

    Exactly the state ``_captcha_retry._give_up_and_leave`` leaves behind, reached through
    the two repository calls that rule makes rather than by writing the columns by hand.
    """
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, _CHANNEL)
    await create_account(
        AccountCreate(account_id=_ACCOUNT, label=_ACCOUNT, session_name=_ACCOUNT),
    )
    await upsert_readiness(_ACCOUNT, _CHANNEL, joined=True, captcha_passed=False, ready=False)
    await stamp_captcha_retry(_ACCOUNT, _CHANNEL)
    await mark_captcha_gave_up(_ACCOUNT, _CHANNEL)
    return campaign.campaign_id


@pytest.mark.asyncio
async def test_re_linking_lets_a_given_up_pair_be_onboarded_again() -> None:
    """The verdict is what ``_onboard_pair._join_and_classify`` refuses on, so it must go.

    The give-up rule unlinks the channel once every serving account has walked out, and the
    hint beside that line tells the operator to link it again. With the verdict surviving,
    the channel comes back carrying pairs onboarding refuses forever — the shape of the
    ``rejoin_gave_up`` defect the reset's own comment block was written for.
    """
    campaign_id = await _a_pair_that_gave_up_on_the_captcha()
    await deactivate_channel(campaign_id, _CHANNEL)

    await link_channel_to_campaign(campaign_id, _CHANNEL)

    readiness = await fetch_readiness(_ACCOUNT, _CHANNEL)
    assert readiness is not None
    assert readiness.captcha_gave_up is False


@pytest.mark.asyncio
async def test_re_linking_hands_the_whole_captcha_budget_back() -> None:
    """Clearing the verdict alone would retire the pair again on the very first tick.

    ``_captcha_retry.retry_spent`` reads a ``checked_at`` newer than the stamp as "the
    authorised re-solve came back and lost", and any onboarding pass after the re-link
    writes exactly that. A surviving stamp therefore means one onboarding attempt and then
    an immediate give-up, logged "2/2" for a retry nobody granted — the wrong-episode trap
    ``retry_owed`` documents. The stamp is the timeline's anchor, so it restarts with it.
    """
    campaign_id = await _a_pair_that_gave_up_on_the_captcha()
    await deactivate_channel(campaign_id, _CHANNEL)

    await link_channel_to_campaign(campaign_id, _CHANNEL)

    assert _captcha_columns() == (None, 0)


@pytest.mark.asyncio
async def test_re_linking_does_not_disturb_a_pair_that_never_met_a_guardian_bot() -> None:
    """The reset writes the NULL/0 already there — no pair is handed anything it lacked."""
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, _CHANNEL)
    await create_account(
        AccountCreate(account_id=_ACCOUNT, label=_ACCOUNT, session_name=_ACCOUNT),
    )
    await upsert_readiness(_ACCOUNT, _CHANNEL, joined=True, captcha_passed=True, ready=True)
    await deactivate_channel(campaign.campaign_id, _CHANNEL)

    await link_channel_to_campaign(campaign.campaign_id, _CHANNEL)

    assert _captcha_columns() == (None, 0)


@pytest.mark.asyncio
async def test_a_banned_pair_stays_banned_across_the_captcha_reset() -> None:
    """``banned`` (#30) is sticky by design and stays out of the fresh start.

    Its own hint sends the operator after another ACCOUNT rather than another link, which is
    what makes it different from every budget cleared beside it.
    """
    campaign_id = await _a_pair_that_gave_up_on_the_captcha()
    await mark_pair_banned(_ACCOUNT, _CHANNEL)
    await deactivate_channel(campaign_id, _CHANNEL)

    await link_channel_to_campaign(campaign_id, _CHANNEL)

    readiness = await fetch_readiness(_ACCOUNT, _CHANNEL)
    assert readiness is not None
    assert (readiness.banned, readiness.captcha_gave_up) == (True, False)


@pytest.mark.asyncio
async def test_an_operator_skip_survives_the_captcha_reset() -> None:
    """``human_skipped`` (#148) is the operator's own decision, not a budget the rule spent.

    Linking a channel says "try this channel again", not "put back the account I took out of
    it"; only the operator's own un-skip does that.
    """
    campaign_id = await _a_pair_that_gave_up_on_the_captcha()
    await mark_human_skipped(_ACCOUNT, _CHANNEL)
    await deactivate_channel(campaign_id, _CHANNEL)

    await link_channel_to_campaign(campaign_id, _CHANNEL)

    readiness = await fetch_readiness(_ACCOUNT, _CHANNEL)
    assert readiness is not None
    assert (readiness.human_skipped, readiness.captcha_gave_up) == (True, False)
