"""The roster swap: one ban, one reserve, one transaction.

The interesting property is negative — a second call for the same ban must not reach
the pool. It is claimed by the conditional ``state = 'active'`` update the transaction
opens with, which is why these tests call the function twice rather than reasoning about
who could call it twice.

The two ways of taking over nobody are told apart on purpose: ``claimed`` says whether
THIS call wrote the ban, so a full pool and a lost race are not both reported to the
operator as "the reserve pool is empty".
"""

from __future__ import annotations

import pytest

from core.db import create_account
from core.repositories.neuroshilling import (
    count_substitutions,
    create_campaign,
    list_campaign_accounts,
    load_scenario,
    replace_scenario,
    substitute_banned_account,
    update_campaign,
)
from schemas.accounts import AccountCreate
from schemas.neuroshilling import (
    NeuroshillingAccountAssignment,
    NeuroshillingCampaignCreate,
    NeuroshillingCampaignUpdate,
)
from schemas.neuroshilling_scenario import NeuroshillingRoleInput


async def _roster(campaign_id: str, *reserves: str) -> str:
    """A campaign whose one role is played by ``acc-1``, plus ``reserves`` in the pool."""
    await replace_scenario(campaign_id, [NeuroshillingRoleInput(name="R")], [])
    roles, _steps = await load_scenario(campaign_id)
    for account_id in ("acc-1", *reserves):
        await create_account(
            AccountCreate(account_id=account_id, label=account_id, session_name=account_id),
        )
    await update_campaign(
        campaign_id,
        NeuroshillingCampaignUpdate(
            name="Promo",
            accounts=[
                NeuroshillingAccountAssignment(account_id="acc-1", role_id=roles[0].role_id),
                *(
                    NeuroshillingAccountAssignment(account_id=account_id, is_reserve=True)
                    for account_id in reserves
                ),
            ],
        ),
    )
    return roles[0].role_id


async def _seeded(*reserves: str) -> tuple[str, str]:
    created = await create_campaign(NeuroshillingCampaignCreate(name="Promo"))
    return created.campaign_id, await _roster(created.campaign_id, *reserves)


@pytest.mark.asyncio
async def test_the_oldest_reserve_takes_the_banned_account_s_role() -> None:
    campaign_id, role_id = await _seeded("res-1", "res-2")

    assert await substitute_banned_account(campaign_id, "acc-1") == (True, "res-1")

    roster = {row.account_id: row for row in await list_campaign_accounts(campaign_id)}
    assert roster["acc-1"].state == "banned"
    assert (roster["res-1"].role_id, roster["res-1"].is_reserve) == (role_id, False)
    assert roster["res-2"].is_reserve is True
    assert await count_substitutions(campaign_id) == 1


@pytest.mark.asyncio
async def test_a_second_call_for_one_ban_spends_nothing() -> None:
    """The ban is CLAIMED before the pool is looked at, so the retry finds it gone."""
    campaign_id, _role_id = await _seeded("res-1", "res-2")

    first = await substitute_banned_account(campaign_id, "acc-1")
    second = await substitute_banned_account(campaign_id, "acc-1")

    # The retry did not claim the ban, which is what says the pool was never asked.
    assert (first, second) == ((True, "res-1"), (False, None))
    roster = {row.account_id: row for row in await list_campaign_accounts(campaign_id)}
    assert roster["res-2"].is_reserve is True
    assert await count_substitutions(campaign_id) == 1


@pytest.mark.asyncio
async def test_an_empty_pool_still_records_the_ban() -> None:
    """The account is finished whether or not anything replaced it.

    Without the row the next run would read it back off the roster and deal it lines
    again, because the halt set it was in does not survive the process.
    """
    campaign_id, _role_id = await _seeded()

    # Claimed by this call, and no stand-in: the pool really is what was empty.
    assert await substitute_banned_account(campaign_id, "acc-1") == (True, None)

    roster = {row.account_id: row for row in await list_campaign_accounts(campaign_id)}
    assert roster["acc-1"].state == "banned"
    # Counted off ``replaced_by_account_id``, which nothing wrote.
    assert await count_substitutions(campaign_id) == 0


@pytest.mark.asyncio
async def test_a_promoted_account_can_itself_be_replaced() -> None:
    """Promotion clears ``is_reserve``, so the stand-in is an ordinary player now."""
    campaign_id, role_id = await _seeded("res-1", "res-2")
    await substitute_banned_account(campaign_id, "acc-1")

    assert await substitute_banned_account(campaign_id, "res-1") == (True, "res-2")

    roster = {row.account_id: row for row in await list_campaign_accounts(campaign_id)}
    assert roster["res-2"].role_id == role_id
    assert await count_substitutions(campaign_id) == 2


@pytest.mark.asyncio
async def test_a_roster_save_keeps_the_ban_and_its_replacement() -> None:
    """``state`` and ``replaced_by_account_id`` are engine-written, not form-written."""
    campaign_id, role_id = await _seeded("res-1")
    await substitute_banned_account(campaign_id, "acc-1")

    await update_campaign(
        campaign_id,
        NeuroshillingCampaignUpdate(
            name="Promo",
            accounts=[
                NeuroshillingAccountAssignment(account_id="acc-1", role_id=role_id),
                NeuroshillingAccountAssignment(account_id="res-1", role_id=role_id),
            ],
        ),
    )

    roster = {row.account_id: row for row in await list_campaign_accounts(campaign_id)}
    assert roster["acc-1"].state == "banned"
    assert await count_substitutions(campaign_id) == 1
