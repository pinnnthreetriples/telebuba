"""A dead key seen anywhere starts one session check at once; never without a proxy."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from schemas.accounts import AccountRead
from schemas.logs import LogEntry
from services import session_death_watch as watch

if TYPE_CHECKING:
    from collections.abc import Iterator

    from schemas.accounts import AccountCheckRequest


def _entry(event: str, account_id: str | None = "acc-1", **extra: object) -> LogEntry:
    return LogEntry(
        id=1,
        created_at="2026-11-16T10:00:00+00:00",
        level="ERROR",
        status="error",
        account_id=account_id,
        event=event,
        extra=extra,
    )


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        (_entry("warming_telegram_set_online_failed", error_type="session_dead"), "acc-1"),
        (_entry("chat_broadcast_telegram_send_failed", error_type="account_deactivated"), "acc-1"),
        (_entry("session_audit_failed", code="session_dead"), "acc-1"),
        (_entry("warming_telegram_set_online_failed", error_type="FloodWaitError"), None),
        (_entry("session_audit_failed", code="unavailable"), None),
        (_entry("warming_telegram_set_online_failed", None, error_type="session_dead"), None),
        (_entry("account_checked", error_type="session_dead"), None),
    ],
)
def test_which_rows_say_a_key_is_dead(entry: LogEntry, expected: str | None) -> None:
    assert watch.death_signal(entry) == expected


def _account(**fields: object) -> AccountRead:
    values: dict[str, object] = {
        "account_id": "acc-1",
        "status": "alive",
        "proxy_id": "proxy-1",
        "created_at": "2026-10-31T00:00:00+00:00",
        "updated_at": "2026-10-31T00:00:00+00:00",
    }
    return AccountRead.model_validate(values | fields)


@pytest.fixture
def checks(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, object]]:
    state: dict[str, object] = {"account": _account(), "checked": [], "logged": []}
    watch.reset_for_tests()

    async def _fetch(_account_id: str) -> AccountRead | None:
        return state["account"]  # ty: ignore[invalid-return-type]

    async def _check(request: AccountCheckRequest) -> object:
        state["checked"].append(request.account_id)  # ty: ignore[unresolved-attribute]
        return None

    async def _log(_level: str, event: str, **_kwargs: object) -> None:
        state["logged"].append(event)  # ty: ignore[unresolved-attribute]

    monkeypatch.setattr(watch, "fetch_account", _fetch)
    monkeypatch.setattr(watch, "check_account_session", _check)
    monkeypatch.setattr(watch, "log_event", _log)
    yield state
    watch.reset_for_tests()


async def _settle() -> None:
    await asyncio.gather(*watch._state.pending)


@pytest.mark.asyncio
async def test_a_dead_key_starts_one_check_and_the_cooldown_holds_the_next(
    checks: dict[str, object],
) -> None:
    dead = _entry("warming_telegram_set_online_failed", error_type="session_dead")

    watch._schedule(dead)
    watch._schedule(dead)
    await _settle()

    assert checks["checked"] == ["acc-1"]
    assert checks["logged"] == ["session_death_suspected"]


@pytest.mark.parametrize(
    "account",
    [_account(proxy_id=None), _account(status="unauthorized"), None],
    ids=["no-proxy", "already-dead", "deleted"],
)
@pytest.mark.asyncio
async def test_no_check_without_a_proxy_for_a_known_death_or_a_gone_account(
    checks: dict[str, object], account: AccountRead | None
) -> None:
    # A check connects; a proxyless one would connect from the operator's own IP.
    checks["account"] = account

    watch._schedule(_entry("warming_telegram_set_online_failed", error_type="session_dead"))
    await _settle()

    assert checks["checked"] == []


@pytest.mark.asyncio
async def test_the_watch_reads_the_live_bus_and_stops_cleanly(checks: dict[str, object]) -> None:
    from core.logging import log_event as real_log_event  # noqa: PLC0415 - the real bus

    await watch.start_session_death_watch()
    await real_log_event(
        "ERROR",
        "warming_telegram_set_online_failed",
        account_id="acc-1",
        extra={"error_type": "session_dead"},
    )
    for _ in range(50):
        if checks["checked"]:
            break
        await asyncio.sleep(0.01)
    await watch.shutdown_session_death_watch()

    assert checks["checked"] == ["acc-1"]
