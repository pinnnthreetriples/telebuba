"""Scenario side of the neuroshilling repository: roles and dialogue steps.

The whole scenario is written by ONE call inside ONE transaction. A per-role and
a per-step endpoint would leave a window where ``neuroshilling_steps.role_id``
points at a role the other call has already deleted, and no client-side ordering
closes it.

"Replace" here is a keyed upsert rather than a truncate:

* a role whose incoming key matches a stored ``role_id`` is UPDATED in place, so
  the account roster's ``role_id`` still points somewhere after the save; every
  other incoming role is inserted with a freshly minted id, and the steps are
  rewired to whatever the id turned out to be;
* steps are matched BY POSITION and updated in place, so a regenerated dialogue
  keeps the ``step_id`` its journal rows reference. Only the surplus tail is
  deleted, and its journal rows go with it in the same transaction — SQLite runs
  with ``PRAGMA foreign_keys=ON`` (``core.db``), so ``neuroshilling_messages``
  would otherwise refuse the delete.

Any write to the scenario returns the campaign to ``draft``. That reset is here,
in the same transaction as the rows it invalidates, rather than in a second call
the caller could forget or fail between.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import delete, insert, select, update

from core.db import _get_engine, _now_iso
from core.repositories.neuroshilling._tables import (
    _neuroshilling_campaigns,
    _neuroshilling_messages,
    _neuroshilling_roles,
    _neuroshilling_steps,
)
from schemas.neuroshilling_scenario import NeuroshillingRole, NeuroshillingStep

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.engine import Connection

    from schemas.neuroshilling_scenario import (
        NeuroshillingRoleInput,
        NeuroshillingStepInput,
    )

    # The operator's role key -> the ``role_id`` actually stored for it.
    _RoleIds = dict[str, str]


def _campaign_exists(connection: Connection, campaign_id: str) -> bool:
    statement = select(_neuroshilling_campaigns.c.campaign_id).where(
        _neuroshilling_campaigns.c.campaign_id == campaign_id,
    )
    return connection.execute(statement).first() is not None


def _select_roles(connection: Connection, campaign_id: str) -> list[NeuroshillingRole]:
    statement = (
        select(_neuroshilling_roles)
        .where(_neuroshilling_roles.c.campaign_id == campaign_id)
        .order_by(
            _neuroshilling_roles.c.created_at.asc(),
            _neuroshilling_roles.c.role_id.asc(),
        )
    )
    rows = connection.execute(statement).mappings().all()
    return [NeuroshillingRole.model_validate(dict(row)) for row in rows]


def _select_steps(connection: Connection, campaign_id: str) -> list[NeuroshillingStep]:
    statement = (
        select(_neuroshilling_steps)
        .where(_neuroshilling_steps.c.campaign_id == campaign_id)
        .order_by(_neuroshilling_steps.c.position.asc())
    )
    rows = connection.execute(statement).mappings().all()
    return [NeuroshillingStep.model_validate(dict(row)) for row in rows]


def _load_scenario(campaign_id: str) -> tuple[list[NeuroshillingRole], list[NeuroshillingStep]]:
    with _get_engine().connect() as connection:
        return _select_roles(connection, campaign_id), _select_steps(connection, campaign_id)


async def load_scenario(
    campaign_id: str,
) -> tuple[list[NeuroshillingRole], list[NeuroshillingStep]]:
    """The campaign's roles and steps, both in play order (empty for an unknown campaign).

    One connection for both, so the pair cannot straddle a concurrent replace and
    come back with steps pointing at roles the other half no longer lists.
    """
    return await asyncio.to_thread(_load_scenario, campaign_id)


def _write_roles(
    connection: Connection,
    campaign_id: str,
    roles: Sequence[NeuroshillingRoleInput],
    now: str,
) -> _RoleIds:
    """Upsert the incoming roles, drop the rest, and report where each key landed.

    New roles are stamped one microsecond apart. ``neuroshilling_roles`` has no
    position column — order IS creation order — and a whole generated cast is
    created inside one transaction, so a single shared timestamp would leave the
    read order to the tie-break on a random uuid: the operator's role chips would
    reshuffle on every reload.
    """
    statement = select(_neuroshilling_roles.c.role_id).where(
        _neuroshilling_roles.c.campaign_id == campaign_id,
    )
    stored = {str(role_id) for (role_id,) in connection.execute(statement)}
    base = datetime.fromisoformat(now)
    role_ids: _RoleIds = {}
    kept: set[str] = set()
    for index, role in enumerate(roles):
        # A role with no key of its own still needs one the mapping can hold, or two
        # keyless roles would collide and the second would steal the first's steps.
        key = role.role_id if role.role_id is not None else f"#{index}"
        values = {"name": role.name, "description": role.description}
        if role.role_id in stored and role.role_id not in kept:
            stored_id = str(role.role_id)
            connection.execute(
                update(_neuroshilling_roles)
                .where(_neuroshilling_roles.c.role_id == stored_id)
                .values(**values),
            )
        else:
            stored_id = uuid4().hex
            connection.execute(
                insert(_neuroshilling_roles).values(
                    role_id=stored_id,
                    campaign_id=campaign_id,
                    created_at=(base + timedelta(microseconds=index)).isoformat(),
                    **values,
                ),
            )
        kept.add(stored_id)
        role_ids[key] = stored_id
    connection.execute(
        delete(_neuroshilling_roles).where(
            _neuroshilling_roles.c.campaign_id == campaign_id,
            _neuroshilling_roles.c.role_id.notin_(kept),
        ),
    )
    return role_ids


def _step_values(
    step: NeuroshillingStepInput,
    role_ids: _RoleIds,
    now: str,
) -> dict[str, object]:
    """The columns a step write sets, with its role key resolved to a stored id.

    An unresolvable key becomes ``NULL`` rather than an error: ``role_id`` is a
    real foreign key, so a key naming nothing would otherwise be an
    ``IntegrityError`` the operator sees as a 500. An unassigned step is a state
    the approval gate already refuses, which is where they are told about it.
    """
    return {
        "kind": step.kind,
        "role_id": None if step.role_id is None else role_ids.get(step.role_id),
        "text": step.text,
        "reply_to_position": step.reply_to_position,
        "target_position": step.target_position,
        "emoji": step.emoji,
        "delay_min_seconds": step.delay_min_seconds,
        "delay_max_seconds": step.delay_max_seconds,
        "updated_at": now,
    }


def _drop_steps_beyond(connection: Connection, campaign_id: str, keep: int) -> None:
    """Delete the tail the new scenario is shorter than, journal rows included."""
    surplus = select(_neuroshilling_steps.c.step_id).where(
        _neuroshilling_steps.c.campaign_id == campaign_id,
        _neuroshilling_steps.c.position > keep,
    )
    connection.execute(
        delete(_neuroshilling_messages).where(_neuroshilling_messages.c.step_id.in_(surplus)),
    )
    connection.execute(
        delete(_neuroshilling_steps).where(
            _neuroshilling_steps.c.campaign_id == campaign_id,
            _neuroshilling_steps.c.position > keep,
        ),
    )


def _write_steps(
    connection: Connection,
    campaign_id: str,
    steps: Sequence[NeuroshillingStepInput],
    role_ids: _RoleIds,
    now: str,
) -> None:
    """Write the dialogue, position by position, reusing the row already at each one."""
    statement = select(_neuroshilling_steps.c.position, _neuroshilling_steps.c.step_id).where(
        _neuroshilling_steps.c.campaign_id == campaign_id,
    )
    stored = {int(position): str(step_id) for position, step_id in connection.execute(statement)}
    _drop_steps_beyond(connection, campaign_id, len(steps))
    for index, step in enumerate(steps):
        position = index + 1
        values = _step_values(step, role_ids, now)
        step_id = stored.get(position)
        if step_id is None:
            connection.execute(
                insert(_neuroshilling_steps).values(
                    step_id=uuid4().hex,
                    campaign_id=campaign_id,
                    position=position,
                    created_at=now,
                    **values,
                ),
            )
        else:
            connection.execute(
                update(_neuroshilling_steps)
                .where(_neuroshilling_steps.c.step_id == step_id)
                .values(**values),
            )


def _set_scenario_status(
    connection: Connection,
    campaign_id: str,
    status: str,
    *,
    clear_media_step: bool = False,
    expected_updated_at: str | None = None,
) -> bool:
    """Write the status; ``False`` = no row matched, so it is missing or has moved on."""
    values: dict[str, object] = {"scenario_status": status, "updated_at": _now_iso()}
    if clear_media_step:
        values["media_step_position"] = None
    statement = update(_neuroshilling_campaigns).where(
        _neuroshilling_campaigns.c.campaign_id == campaign_id,
    )
    if expected_updated_at is not None:
        statement = statement.where(
            _neuroshilling_campaigns.c.updated_at == expected_updated_at,
        )
    return connection.execute(statement.values(**values)).rowcount > 0


def _replace_scenario(
    campaign_id: str,
    roles: Sequence[NeuroshillingRoleInput],
    steps: Sequence[NeuroshillingStepInput],
    *,
    clear_media_step: bool,
) -> bool:
    with _get_engine().begin() as connection:
        if not _campaign_exists(connection, campaign_id):
            return False
        now = _now_iso()
        role_ids = _write_roles(connection, campaign_id, roles, now)
        _write_steps(connection, campaign_id, steps, role_ids, now)
        # In the SAME transaction as the rows it invalidates: an approval that
        # outlived the dialogue it vouched for is exactly what the gate is for.
        _set_scenario_status(connection, campaign_id, "draft", clear_media_step=clear_media_step)
    return True


async def replace_scenario(
    campaign_id: str,
    roles: Sequence[NeuroshillingRoleInput],
    steps: Sequence[NeuroshillingStepInput],
    *,
    clear_media_step: bool = False,
) -> bool:
    """Write the whole scenario atomically and return it to ``draft``.

    ``clear_media_step`` also writes ``media_step_position=NULL``, in the same
    UPDATE as the status. It is off by default because a hand-edited scenario
    keeps the slot the operator chose; only a caller that replaces every line with
    text the operator has never read turns it on, since the position would
    otherwise survive onto a step nobody aimed it at.

    ``False`` means there is no such campaign — checked inside the transaction, so
    a campaign deleted between the caller's read and this write cannot leave
    orphan roles behind.
    """
    return await asyncio.to_thread(
        _replace_scenario,
        campaign_id,
        roles,
        steps,
        clear_media_step=clear_media_step,
    )


def _approve_scenario(campaign_id: str, expected_updated_at: str) -> bool:
    with _get_engine().begin() as connection:
        return _set_scenario_status(
            connection,
            campaign_id,
            "approved",
            expected_updated_at=expected_updated_at,
        )


async def approve_scenario(campaign_id: str, *, expected_updated_at: str) -> bool:
    """Mark the campaign approved. The ONLY writer of ``scenario_status='approved'``.

    Validation is the service's, deliberately: the rule is about roles pointing at
    accounts and positions pointing backwards, none of which is a column
    constraint. What is enforced HERE is that no other write path can set it —
    ``update_campaign``'s editable-column tuple excludes ``scenario_status``, and
    ``replace_scenario`` only ever writes ``draft``.

    ``expected_updated_at`` is the campaign's stamp as the validating read saw it, and
    the write lands only while the row still carries it. The stamp moves in the same
    transaction as each write the gate reads from: the roles and steps here, the topic,
    the media slot and the roster in ``_campaigns._update_campaign``, the run state in
    ``_campaigns._set_run_state``. So an edit — or a launch — that arrived while the
    service was validating leaves nothing for this to match. ``False`` therefore means
    one of two things, no campaign or a moved one, and the caller reads the row back
    rather than being told which.
    """
    return await asyncio.to_thread(_approve_scenario, campaign_id, expected_updated_at)
