"""Warming state-row transitions — the single writer of ``warming_account_state``."""

from __future__ import annotations

from typing import Final, cast

from core.db import fetch_account, fetch_warming_state, upsert_warming_state
from schemas.warming import (
    WarmingAccountState,
    WarmingState,
    WarmingStateRecord,
    WarmingStateWrite,
    warming_health,
)
from services.warming.board import _to_card


class _Sentinel:
    """Marker type so ``_set_state`` can distinguish "carry current" from "set to None"."""


_SENTINEL: Final = _Sentinel()


async def _set_state(  # noqa: PLR0913 - explicit state fields read clearer than a bag model here.
    account_id: str,
    state: WarmingState,
    *,
    last_event: str | None = None,
    last_cycle_at: str | None = None,
    next_run_at: str | None | _Sentinel = _SENTINEL,
    increment_cycle: bool = False,
    last_error: str | None | _Sentinel = _SENTINEL,
    last_action: str | None | _Sentinel = _SENTINEL,
    last_channel: str | None | _Sentinel = _SENTINEL,
    heartbeat_at: str | None | _Sentinel = _SENTINEL,
    started_at: str | None | _Sentinel = _SENTINEL,
    stopped_at: str | None | _Sentinel = _SENTINEL,
    flood_wait_seconds: int | None | _Sentinel = _SENTINEL,
    flood_wait_until: str | None | _Sentinel = _SENTINEL,
    proxy_snapshot: str | None | _Sentinel = _SENTINEL,
    daily_actions: int | _Sentinel = _SENTINEL,
    daily_count_date: str | None | _Sentinel = _SENTINEL,
    quarantine_count: int | _Sentinel = _SENTINEL,
    run_id: str | None | _Sentinel = _SENTINEL,
) -> WarmingStateRecord:
    current = await fetch_warming_state(account_id)
    cycles = current.cycles_completed if current else 0
    if increment_cycle:
        cycles += 1

    def _resolve(value: object, field: str) -> object:
        if value is _SENTINEL:
            return getattr(current, field) if current else None
        return value

    return await upsert_warming_state(
        WarmingStateWrite(
            account_id=account_id,
            state=state,
            cycles_completed=cycles,
            last_event=last_event if last_event is not None else _carry(current, "last_event"),
            last_cycle_at=(
                last_cycle_at if last_cycle_at is not None else _carry(current, "last_cycle_at")
            ),
            next_run_at=cast("str | None", _resolve(next_run_at, "next_run_at")),
            last_error=cast("str | None", _resolve(last_error, "last_error")),
            last_action=cast("str | None", _resolve(last_action, "last_action")),
            last_channel=cast("str | None", _resolve(last_channel, "last_channel")),
            heartbeat_at=cast("str | None", _resolve(heartbeat_at, "heartbeat_at")),
            started_at=cast("str | None", _resolve(started_at, "started_at")),
            stopped_at=cast("str | None", _resolve(stopped_at, "stopped_at")),
            flood_wait_seconds=cast(
                "int | None",
                _resolve(flood_wait_seconds, "flood_wait_seconds"),
            ),
            flood_wait_until=cast(
                "str | None",
                _resolve(flood_wait_until, "flood_wait_until"),
            ),
            proxy_snapshot=cast("str | None", _resolve(proxy_snapshot, "proxy_snapshot")),
            daily_actions=cast("int", _resolve(daily_actions, "daily_actions") or 0),
            daily_count_date=cast("str | None", _resolve(daily_count_date, "daily_count_date")),
            quarantine_count=cast("int", _resolve(quarantine_count, "quarantine_count") or 0),
            run_id=cast("str | None", _resolve(run_id, "run_id")),
        ),
    )


def _carry(record: WarmingStateRecord | None, field: str) -> str | None:
    if record is None:
        return None
    value = getattr(record, field)
    return value if isinstance(value, str) else None


async def _current_card(account_id: str) -> WarmingAccountState:
    account = await fetch_account(account_id)
    record = await fetch_warming_state(account_id)
    if account is not None:
        return _to_card(account, record)
    state: WarmingState = record.state if record else "idle"
    return WarmingAccountState(
        account_id=account_id,
        label=account_id,
        state=state,
        health=warming_health(state),
        cycles_completed=record.cycles_completed if record else 0,
        last_event=record.last_event if record else None,
        last_cycle_at=record.last_cycle_at if record else None,
        next_run_at=record.next_run_at if record else None,
        updated_at=record.updated_at if record else None,
        last_error=record.last_error if record else None,
        last_action=record.last_action if record else None,
        last_channel=record.last_channel if record else None,
        heartbeat_at=record.heartbeat_at if record else None,
        started_at=record.started_at if record else None,
        stopped_at=record.stopped_at if record else None,
        flood_wait_seconds=record.flood_wait_seconds if record else None,
        flood_wait_until=record.flood_wait_until if record else None,
        proxy_snapshot=record.proxy_snapshot if record else None,
        daily_actions=record.daily_actions if record else 0,
        daily_count_date=record.daily_count_date if record else None,
        quarantine_count=record.quarantine_count if record else 0,
    )
