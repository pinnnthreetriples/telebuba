"""``_delete_account`` against every table that holds a foreign key to the account.

The FKs carry no ``ON DELETE CASCADE`` and ``PRAGMA foreign_keys`` is on, so a child
table the delete forgets turns deleting an account into an ``IntegrityError``. The
guard below asks the schema rather than a hand-kept list, so the next table to grow
such a key fails here instead of in the operator's Accounts page.
"""

from __future__ import annotations

import asyncio
import importlib
import pkgutil
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import event

import core.repositories
from core.db import _get_engine, _metadata, configure_database, create_account
from core.repositories.accounts import _delete_account
from core.repositories.neuroshilling import (
    count_substitutions,
    create_campaign,
    fetch_campaign,
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

if TYPE_CHECKING:
    from pathlib import Path


def _tables_referencing_accounts() -> set[str]:
    # Every repository module registers its tables on import; walk them all so a
    # table living in a package nothing else has imported yet is still seen.
    for module in pkgutil.walk_packages(core.repositories.__path__, "core.repositories."):
        importlib.import_module(module.name)
    return {
        table.name
        for table in _metadata.tables.values()
        for key in table.foreign_keys
        if key.target_fullname == "accounts.account_id"
    }


def test_delete_account_purges_every_table_with_a_foreign_key_to_accounts(
    tmp_path: Path,
) -> None:
    configure_database(tmp_path / "telebuba.db")
    referencing = _tables_referencing_accounts()
    assert "neuroshilling_accounts" in referencing
    statements: list[str] = []

    def _record(*args: object) -> None:
        statements.append(str(args[2]).upper())

    engine = _get_engine()
    event.listen(engine, "before_cursor_execute", _record)
    try:
        _delete_account("acc-none")
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    deleted_from = {
        statement.split()[2].lower() for statement in statements if statement.startswith("DELETE")
    }
    assert referencing <= deleted_from


async def _campaign_played_by(account_id: str, reserve: str) -> str:
    created = await create_campaign(NeuroshillingCampaignCreate(name="Promo"))
    await replace_scenario(created.campaign_id, [NeuroshillingRoleInput(name="R")], [])
    roles, _steps = await load_scenario(created.campaign_id)
    current = await fetch_campaign(created.campaign_id)
    assert current is not None
    await update_campaign(
        created.campaign_id,
        NeuroshillingCampaignUpdate(
            expected_updated_at=current.updated_at,
            name="Promo",
            accounts=[
                NeuroshillingAccountAssignment(account_id=account_id, role_id=roles[0].role_id),
                NeuroshillingAccountAssignment(account_id=reserve, is_reserve=True),
            ],
        ),
    )
    return created.campaign_id


@pytest.mark.asyncio
async def test_delete_account_takes_its_neuroshilling_roster_rows(tmp_path: Path) -> None:
    configure_database(tmp_path / "telebuba.db")
    for account_id in ("acc-gone", "acc-keep"):
        await create_account(AccountCreate(account_id=account_id))
    campaign_id = await _campaign_played_by("acc-gone", reserve="acc-keep")

    await asyncio.to_thread(_delete_account, "acc-gone")

    assert [row.account_id for row in await list_campaign_accounts(campaign_id)] == ["acc-keep"]


@pytest.mark.asyncio
async def test_deleting_a_promoted_reserve_leaves_the_ban_s_substitution_spent(
    tmp_path: Path,
) -> None:
    """``replaced_by_account_id`` is history, not a live link, and is not an FK.

    Clearing it would hand the banned row's one substitution back, so a second caller
    for the same ban could spend another reserve — the invariant
    ``substitute_banned_account`` exists to hold.
    """
    configure_database(tmp_path / "telebuba.db")
    for account_id in ("acc-banned", "acc-reserve"):
        await create_account(AccountCreate(account_id=account_id))
    campaign_id = await _campaign_played_by("acc-banned", reserve="acc-reserve")
    assert await substitute_banned_account(campaign_id, "acc-banned") == (True, "acc-reserve")

    await asyncio.to_thread(_delete_account, "acc-reserve")

    (banned,) = await list_campaign_accounts(campaign_id)
    assert banned.state == "banned"
    assert await count_substitutions(campaign_id) == 1
