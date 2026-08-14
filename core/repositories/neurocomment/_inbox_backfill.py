"""Durable cursor planning and checkpoints for neurocomment gap backfill."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import NamedTuple

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core.db import _get_engine
from core.repositories.neurocomment._tables import _neurocomment_cursors


class BackfillPlan(NamedTuple):
    floor_post_id: int
    before_post_id: int | None


def _prepare_backfill(channels: list[str], interval_seconds: float) -> dict[str, BackfillPlan]:
    now = datetime.now(UTC)
    now_iso = now.isoformat()
    plans: dict[str, BackfillPlan] = {}
    with _get_engine().begin() as connection:
        for channel in channels:
            connection.execute(
                sqlite_insert(_neurocomment_cursors)
                .values(channel=channel, last_post_id=0, updated_at=now_iso)
                .on_conflict_do_nothing(),
            )
            row = (
                connection.execute(
                    select(_neurocomment_cursors).where(_neurocomment_cursors.c.channel == channel),
                )
                .mappings()
                .one()
            )
            retry_at = row["backfill_retry_at"]
            if retry_at and datetime.fromisoformat(str(retry_at)) > now:
                continue
            floor = row["backfill_floor_post_id"]
            if floor is None:
                success_at = row["backfill_success_at"]
                if success_at and now - datetime.fromisoformat(str(success_at)) < timedelta(
                    seconds=interval_seconds,
                ):
                    continue
                floor = int(row["last_post_id"])
                connection.execute(
                    update(_neurocomment_cursors)
                    .where(_neurocomment_cursors.c.channel == channel)
                    .values(
                        backfill_floor_post_id=floor,
                        backfill_before_post_id=None,
                        updated_at=now_iso,
                    ),
                )
            before = row["backfill_before_post_id"]
            plans[channel] = BackfillPlan(int(floor), None if before is None else int(before))
    return plans


async def prepare_backfill(channels: list[str], interval_seconds: float) -> dict[str, BackfillPlan]:
    return await asyncio.to_thread(_prepare_backfill, channels, interval_seconds)


def _checkpoint_backfill(
    channel: str,
    *,
    before_post_id: int | None,
    success: bool,
    retry_seconds: float,
) -> None:
    now = datetime.now(UTC)
    values: dict[str, object] = {"updated_at": now.isoformat()}
    if success:
        values.update(
            backfill_floor_post_id=None,
            backfill_before_post_id=None,
            backfill_success_at=now.isoformat(),
            backfill_retry_at=None,
        )
    else:
        values.update(
            backfill_before_post_id=before_post_id,
            backfill_retry_at=(now + timedelta(seconds=retry_seconds)).isoformat(),
        )
    with _get_engine().begin() as connection:
        connection.execute(
            update(_neurocomment_cursors)
            .where(_neurocomment_cursors.c.channel == channel)
            .values(**values),
        )


async def checkpoint_backfill(
    channel: str,
    *,
    before_post_id: int | None,
    success: bool,
    retry_seconds: float,
) -> None:
    await asyncio.to_thread(
        _checkpoint_backfill,
        channel,
        before_post_id=before_post_id,
        success=success,
        retry_seconds=retry_seconds,
    )
