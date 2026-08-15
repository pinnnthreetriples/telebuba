"""Neuroshilling campaign + roster repository tests."""

from __future__ import annotations

from typing import Any

import pytest

from core.db import create_account
from core.repositories.neuroshilling import (
    create_campaign,
    delete_campaign,
    fetch_campaign,
    list_campaign_accounts,
    list_campaigns,
    list_running_campaign_account_names,
    update_campaign,
)
from schemas.accounts import AccountCreate
from schemas.neuroshilling import (
    NeuroshillingAccountAssignment,
    NeuroshillingCampaignCreate,
    NeuroshillingCampaignUpdate,
)


async def _account(account_id: str) -> None:
    await create_account(
        AccountCreate(account_id=account_id, label=account_id.upper(), session_name=account_id),
    )


def _update(**overrides: Any) -> NeuroshillingCampaignUpdate:
    payload: dict[str, Any] = {"name": "Promo", **overrides}
    return NeuroshillingCampaignUpdate(**payload)


@pytest.mark.asyncio
async def test_a_new_campaign_starts_idle_as_a_draft() -> None:
    created = await create_campaign(NeuroshillingCampaignCreate(name="Promo"))

    assert created.status == "idle"
    assert created.scenario_status == "draft"
    assert created.run_id is None
    assert created.mode == "campaign"
    # The column defaults are what a fresh campaign is made of, and they are the
    # project's own neurocomment ceilings rather than something more permissive.
    assert (created.messages_per_hour, created.messages_per_chat_per_day) == (10, 3)
    assert await fetch_campaign(created.campaign_id) == created


@pytest.mark.asyncio
async def test_campaigns_list_in_creation_order() -> None:
    first = await create_campaign(NeuroshillingCampaignCreate(name="A"))
    second = await create_campaign(NeuroshillingCampaignCreate(name="B"))

    listed = await list_campaigns()

    assert [item.campaign_id for item in listed.campaigns] == [
        first.campaign_id,
        second.campaign_id,
    ]


@pytest.mark.asyncio
async def test_fetching_an_unknown_campaign_returns_none() -> None:
    assert await fetch_campaign("nope") is None


@pytest.mark.asyncio
async def test_update_writes_every_editable_column() -> None:
    campaign = await create_campaign(NeuroshillingCampaignCreate(name="Promo"))

    updated = await update_campaign(
        campaign.campaign_id,
        _update(
            name="Renamed",
            mode="revive",
            topic="delivery service",
            targets_raw="@one @two",
            unique_messages=False,
            use_chat_context=True,
            media_message_link="https://t.me/chan/7",
            media_step_position=2,
            pause_min_seconds=5,
            pause_max_seconds=25,
            messages_per_hour=4,
            messages_per_chat_per_day=0,
            total_per_account=9,
            reserve_enabled=True,
            autoresponder="neurodialog",
            reply_to_humans=True,
            reply_activity="active",
            listen_minutes=15,
        ),
    )

    assert updated is not None
    persisted = await fetch_campaign(campaign.campaign_id)
    assert persisted == updated
    assert updated.name == "Renamed"
    assert updated.mode == "revive"
    assert updated.targets_raw == "@one @two"
    assert updated.unique_messages is False
    assert updated.use_chat_context is True
    assert updated.total_per_account == 9
    assert updated.reply_activity == "active"
    assert updated.updated_at >= campaign.updated_at


@pytest.mark.asyncio
async def test_updating_an_unknown_campaign_returns_none() -> None:
    assert await update_campaign("nope", _update()) is None


@pytest.mark.asyncio
async def test_the_roster_is_replaced_wholesale_but_keeps_engine_owned_state() -> None:
    """Dropped accounts go; surviving ones keep what the ENGINE wrote about them.

    ``state`` and ``replaced_by_account_id`` are set by a ban, never by the form,
    and the save replaces the roster with a DELETE + INSERT — which would reset
    them just as effectively as making them editable columns would.
    """
    await _account("acc-1")
    await _account("acc-2")
    campaign = await create_campaign(NeuroshillingCampaignCreate(name="Promo"))

    await update_campaign(
        campaign.campaign_id,
        _update(
            accounts=[
                NeuroshillingAccountAssignment(account_id="acc-1"),
                NeuroshillingAccountAssignment(account_id="acc-2", is_reserve=True),
            ],
        ),
    )
    assert [
        (item.account_id, item.is_reserve)
        for item in await list_campaign_accounts(campaign.campaign_id)
    ] == [
        ("acc-1", False),
        ("acc-2", True),
    ]
    await _force_engine_state(campaign.campaign_id, "acc-2", "banned", "acc-1")

    await update_campaign(
        campaign.campaign_id,
        _update(accounts=[NeuroshillingAccountAssignment(account_id="acc-2")]),
    )

    roster = await list_campaign_accounts(campaign.campaign_id)
    assert [(item.account_id, item.is_reserve, item.state) for item in roster] == [
        ("acc-2", False, "banned"),
    ]
    assert await _engine_state(campaign.campaign_id) == {"acc-2": ("banned", "acc-1")}


@pytest.mark.asyncio
async def test_an_account_joining_the_roster_starts_active() -> None:
    """Carrying state forward must not mean a brand-new row inherits anything."""
    await _account("acc-1")
    await _account("acc-2")
    campaign = await create_campaign(NeuroshillingCampaignCreate(name="Promo"))
    await update_campaign(
        campaign.campaign_id,
        _update(accounts=[NeuroshillingAccountAssignment(account_id="acc-1")]),
    )
    await _force_engine_state(campaign.campaign_id, "acc-1", "banned", "acc-2")

    await update_campaign(
        campaign.campaign_id,
        _update(
            accounts=[
                NeuroshillingAccountAssignment(account_id="acc-1"),
                NeuroshillingAccountAssignment(account_id="acc-2"),
            ],
        ),
    )

    assert await _engine_state(campaign.campaign_id) == {
        "acc-1": ("banned", "acc-2"),
        "acc-2": ("active", None),
    }


@pytest.mark.asyncio
async def test_a_repeated_account_in_one_roster_is_stored_once() -> None:
    """The composite primary key would otherwise reject the whole save."""
    await _account("acc-1")
    campaign = await create_campaign(NeuroshillingCampaignCreate(name="Promo"))

    await update_campaign(
        campaign.campaign_id,
        _update(
            accounts=[
                NeuroshillingAccountAssignment(account_id="acc-1"),
                NeuroshillingAccountAssignment(account_id="acc-1", is_reserve=True),
            ],
        ),
    )

    assert [item.account_id for item in await list_campaign_accounts(campaign.campaign_id)] == [
        "acc-1",
    ]


@pytest.mark.asyncio
async def test_one_account_serves_several_campaigns() -> None:
    """Assignment is not exclusion — only a RUNNING campaign holds an account."""
    await _account("acc-1")
    first = await create_campaign(NeuroshillingCampaignCreate(name="A"))
    second = await create_campaign(NeuroshillingCampaignCreate(name="B"))
    roster = [NeuroshillingAccountAssignment(account_id="acc-1")]

    await update_campaign(first.campaign_id, _update(accounts=roster))
    await update_campaign(second.campaign_id, _update(accounts=roster))

    assert len(await list_campaign_accounts(first.campaign_id)) == 1
    assert len(await list_campaign_accounts(second.campaign_id)) == 1


@pytest.mark.asyncio
async def test_only_a_live_campaign_reports_its_accounts_as_held() -> None:
    await _account("acc-1")
    campaign = await create_campaign(NeuroshillingCampaignCreate(name="Promo"))
    await update_campaign(
        campaign.campaign_id,
        _update(accounts=[NeuroshillingAccountAssignment(account_id="acc-1")]),
    )

    assert await list_running_campaign_account_names() == {}

    await _force_status(campaign.campaign_id, "running")

    assert await list_running_campaign_account_names() == {
        "acc-1": (campaign.campaign_id, "Promo"),
    }


@pytest.mark.asyncio
async def test_delete_removes_the_campaign_and_its_roster() -> None:
    await _account("acc-1")
    campaign = await create_campaign(NeuroshillingCampaignCreate(name="Promo"))
    await update_campaign(
        campaign.campaign_id,
        _update(accounts=[NeuroshillingAccountAssignment(account_id="acc-1")]),
    )

    await delete_campaign(campaign.campaign_id)

    assert await fetch_campaign(campaign.campaign_id) is None
    assert await list_campaign_accounts(campaign.campaign_id) == []
    # Idempotent: deleting again is a no-op, not an error.
    await delete_campaign(campaign.campaign_id)


async def _force_status(campaign_id: str, status: str) -> None:
    """Set run state the way the engine will, bypassing the editable-column gate."""
    import asyncio  # noqa: PLC0415

    from sqlalchemy import update as sql_update  # noqa: PLC0415

    from core.db import _get_engine  # noqa: PLC0415
    from core.repositories.neuroshilling._tables import (  # noqa: PLC0415
        _neuroshilling_campaigns,
    )

    def _run() -> None:
        with _get_engine().begin() as connection:
            connection.execute(
                sql_update(_neuroshilling_campaigns)
                .where(_neuroshilling_campaigns.c.campaign_id == campaign_id)
                .values(status=status),
            )

    await asyncio.to_thread(_run)


async def _force_engine_state(
    campaign_id: str,
    account_id: str,
    state: str,
    replaced_by: str | None,
) -> None:
    """Write the roster columns a ban writes — there is no repository call for them."""
    import asyncio  # noqa: PLC0415

    from sqlalchemy import update as sql_update  # noqa: PLC0415

    from core.db import _get_engine  # noqa: PLC0415
    from core.repositories.neuroshilling._tables import _neuroshilling_accounts  # noqa: PLC0415

    def _run() -> None:
        with _get_engine().begin() as connection:
            connection.execute(
                sql_update(_neuroshilling_accounts)
                .where(
                    _neuroshilling_accounts.c.campaign_id == campaign_id,
                    _neuroshilling_accounts.c.account_id == account_id,
                )
                .values(state=state, replaced_by_account_id=replaced_by),
            )

    await asyncio.to_thread(_run)


async def _engine_state(campaign_id: str) -> dict[str, tuple[str, str | None]]:
    """``account_id -> (state, replaced_by_account_id)``; the read model hides the latter."""
    import asyncio  # noqa: PLC0415

    from sqlalchemy import select  # noqa: PLC0415

    from core.db import _get_engine  # noqa: PLC0415
    from core.repositories.neuroshilling._tables import _neuroshilling_accounts  # noqa: PLC0415

    def _run() -> dict[str, tuple[str, str | None]]:
        statement = select(
            _neuroshilling_accounts.c.account_id,
            _neuroshilling_accounts.c.state,
            _neuroshilling_accounts.c.replaced_by_account_id,
        ).where(_neuroshilling_accounts.c.campaign_id == campaign_id)
        with _get_engine().connect() as connection:
            return {
                str(account_id): (str(state), replaced_by)
                for account_id, state, replaced_by in connection.execute(statement)
            }

    return await asyncio.to_thread(_run)
