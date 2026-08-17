"""Seed one runnable campaign, so the engine tests describe behaviour and not setup.

Everything is written through the real repository and the real service, because the
things under test are ordering guarantees ACROSS those layers: a fixture that inserted
rows by hand could not tell whether the row really precedes the send.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NamedTuple

from core.db import create_account
from core.repositories import neuroshilling as repository
from schemas.accounts import AccountCreate
from schemas.neuroshilling import (
    NeuroshillingAccountAssignment,
    NeuroshillingCampaignCreate,
    NeuroshillingCampaignUpdate,
)
from schemas.neuroshilling_scenario import NeuroshillingRoleInput, NeuroshillingStepInput
from schemas.telegram_actions import ActionResult

if TYPE_CHECKING:
    from schemas.neuroshilling_scenario import NeuroshillingRole, NeuroshillingStep


class Seeded(NamedTuple):
    """The ids an engine test needs to assert against."""

    campaign_id: str
    roles: list[NeuroshillingRole]
    steps: list[NeuroshillingStep]
    accounts: list[str]


async def seed_campaign(
    *,
    targets: str = "@alpha",
    accounts: tuple[str, ...] = ("acc-1", "acc-2"),
    reserves: tuple[str, ...] = (),
    steps: list[NeuroshillingStepInput] | None = None,
    approve: bool = True,
    **overrides: Any,
) -> Seeded:
    """A campaign with two roles, a two-line dialogue, a roster and an approval.

    One account per role by default, in roster order, which is what makes the step
    tests deterministic without pinning the rng seam. ``reserves`` are rostered with no
    role and the reserve flag set, which is the substitution pool: the engine leaves
    them out of the cast until a ban promotes one.

    ``reserve_enabled`` defaults to ON here and OFF in the product, because the switch
    is what every ban path runs through and a helper that left it off would make those
    tests describe the switch instead of the path. The off position has a test of its
    own, which passes it explicitly.
    """
    overrides.setdefault("reserve_enabled", True)
    for account_id in (*accounts, *reserves):
        await create_account(
            AccountCreate(account_id=account_id, label=account_id, session_name=account_id),
        )
    created = await repository.create_campaign(NeuroshillingCampaignCreate(name="Promo"))
    campaign_id = created.campaign_id
    role_inputs = [
        NeuroshillingRoleInput(role_id=f"#{index}", name=f"Role {index}")
        for index in range(len(accounts))
    ]
    dialogue = steps if steps is not None else _default_steps(len(accounts))
    await repository.replace_scenario(campaign_id, role_inputs, dialogue)
    stored_roles, stored_steps = await repository.load_scenario(campaign_id)
    await repository.update_campaign(
        campaign_id,
        NeuroshillingCampaignUpdate(
            name="Promo",
            targets_raw=targets,
            accounts=[
                *(
                    NeuroshillingAccountAssignment(
                        account_id=account_id,
                        role_id=stored_roles[index].role_id,
                    )
                    for index, account_id in enumerate(accounts)
                ),
                *(
                    NeuroshillingAccountAssignment(account_id=account_id, is_reserve=True)
                    for account_id in reserves
                ),
            ],
            **overrides,
        ),
    )
    if approve:
        await repository.approve_scenario(campaign_id)
    return Seeded(campaign_id, stored_roles, stored_steps, list(accounts))


def _default_steps(role_count: int) -> list[NeuroshillingStepInput]:
    """One line per role, the second answering the first. Zero delays, so tests run."""
    return [
        NeuroshillingStepInput(
            role_id=f"#{index}",
            text=f"line {index}",
            reply_to_position=None if index == 0 else 1,
            delay_min_seconds=0,
            delay_max_seconds=0,
        )
        for index in range(role_count)
    ]


def sent(message_id: int = 100) -> ActionResult:
    """The gateway's answer to a delivered write."""
    return ActionResult(
        status="ok",
        action_type="post_comment",
        account_id="acc-1",
        message_id=message_id,
    )


def refused(status: str, **fields: Any) -> ActionResult:
    return ActionResult(
        status=status,  # ty: ignore[invalid-argument-type]
        action_type="post_comment",
        account_id="acc-1",
        **fields,
    )
