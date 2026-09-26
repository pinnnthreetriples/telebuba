"""Lifecycle coverage for the all-account inbox supervisor."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from services import inbox_runtime

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable


@pytest_asyncio.fixture(autouse=True)
async def _reset_runtime() -> AsyncIterator[None]:
    await inbox_runtime.shutdown_inbox_runtime()
    inbox_runtime._reset_for_tests()
    yield
    await inbox_runtime.shutdown_inbox_runtime()
    inbox_runtime._reset_for_tests()


def _account(account_id: str, status: str) -> SimpleNamespace:
    return SimpleNamespace(account_id=account_id, status=status)


@pytest.mark.asyncio
async def test_startup_subscribes_each_authorized_account_and_skips_new_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connected: list[str] = []
    stopped: list[str] = []
    monkeypatch.setattr(
        inbox_runtime,
        "list_accounts",
        lambda: _accounts(
            [
                _account("alive", "alive"),
                _account("frozen", "frozen"),
                _account("flood", "flood_wait"),
                _account("network", "network_error"),
                _account("proxy", "proxy_error"),
                _account("unknown", "unknown_error"),
                _account("new", "new"),
                _account("unauthorized", "unauthorized"),
                _account("session-error", "session_error"),
                _account("account-error", "account_error"),
            ],
        ),
    )

    async def subscribe(account_id: str, _callback: object) -> bool:
        connected.append(account_id)
        return True

    async def stop(account_id: str, *, forget: bool = False) -> None:  # noqa: ARG001
        stopped.append(account_id)

    monkeypatch.setattr(inbox_runtime, "subscribe_incoming_messages", subscribe)
    monkeypatch.setattr(inbox_runtime, "stop_incoming_messages", stop)

    await inbox_runtime.reconcile_inboxes_on_startup()
    await _wait_for(lambda: len(connected) == 6)
    await inbox_runtime.shutdown_inbox_runtime()

    expected = {"alive", "frozen", "flood", "network", "proxy", "unknown"}
    assert set(connected) == expected
    assert set(stopped) == expected


@pytest.mark.asyncio
async def test_account_started_after_login_and_stopped_on_logout_or_removal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accounts = [_account("later", "new")]
    connected: list[str] = []
    stopped: list[tuple[str, bool]] = []
    monkeypatch.setattr(inbox_runtime, "list_accounts", lambda: _accounts(accounts))

    async def fetch_account(_account_id: str) -> SimpleNamespace:
        return accounts[0]

    monkeypatch.setattr(inbox_runtime, "fetch_account", fetch_account)

    async def subscribe(account_id: str, _callback: object) -> bool:
        connected.append(account_id)
        return True

    async def stop(account_id: str, *, forget: bool = False) -> None:
        stopped.append((account_id, forget))

    monkeypatch.setattr(inbox_runtime, "subscribe_incoming_messages", subscribe)
    monkeypatch.setattr(inbox_runtime, "stop_incoming_messages", stop)

    await inbox_runtime.reconcile_inboxes_on_startup()
    assert inbox_runtime._STARTUP_TASK is not None
    await inbox_runtime._STARTUP_TASK
    await inbox_runtime.start_account_inbox("later")
    accounts[0].status = "alive"  # phone login or a successful session check
    await inbox_runtime.start_account_inbox("later")
    await inbox_runtime.stop_account_inbox("later")
    await inbox_runtime.stop_account_inbox("later", forget=True)

    assert connected == ["later"]
    assert stopped == [("later", False), ("later", True)]


@pytest.mark.asyncio
async def test_startup_reconcile_does_not_hold_lifespan_on_account_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()

    async def blocked_list_accounts() -> SimpleNamespace:
        entered.set()
        await asyncio.Event().wait()
        return SimpleNamespace(accounts=[])

    monkeypatch.setattr(inbox_runtime, "list_accounts", blocked_list_accounts)
    await inbox_runtime.reconcile_inboxes_on_startup()
    assert inbox_runtime._STARTUP_TASK is not None
    await asyncio.wait_for(entered.wait(), timeout=1)


@pytest.mark.asyncio
async def test_active_listener_health_monitor_rechecks_the_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(inbox_runtime, "_HEALTH_INTERVAL_SECONDS", 0.001)
    monkeypatch.setattr(
        inbox_runtime,
        "list_accounts",
        lambda: _accounts([_account("active", "alive")]),
    )
    checks: list[str] = []
    stopped: list[str] = []

    async def fetch_account(_account_id: str) -> SimpleNamespace:
        return _account("active", "alive")

    monkeypatch.setattr(inbox_runtime, "fetch_account", fetch_account)

    async def subscribe(_account_id: str, _callback: object) -> bool:
        return True

    async def ensure(account_id: str) -> bool:
        checks.append(account_id)
        return True

    async def stop(account_id: str, *, forget: bool = False) -> None:  # noqa: ARG001
        stopped.append(account_id)

    monkeypatch.setattr(inbox_runtime, "subscribe_incoming_messages", subscribe)
    monkeypatch.setattr(inbox_runtime, "ensure_incoming_message_connection", ensure)
    monkeypatch.setattr(inbox_runtime, "stop_incoming_messages", stop)
    await inbox_runtime.reconcile_inboxes_on_startup()
    await _wait_for(lambda: "active" in checks)
    await inbox_runtime.shutdown_inbox_runtime()

    assert checks == ["active"]
    assert stopped == ["active"]


@pytest.mark.asyncio
async def test_missing_handler_is_resubscribed_and_monitor_is_replaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(inbox_runtime, "_HEALTH_INTERVAL_SECONDS", 0.001)
    monkeypatch.setattr(
        inbox_runtime,
        "list_accounts",
        lambda: _accounts([_account("active", "alive")]),
    )
    subscriptions: list[str] = []
    health_checks = 0

    async def fetch_account(_account_id: str) -> SimpleNamespace:
        return _account("active", "alive")

    monkeypatch.setattr(inbox_runtime, "fetch_account", fetch_account)

    async def subscribe(account_id: str, _callback: object) -> bool:
        subscriptions.append(account_id)
        return True

    async def ensure(_account_id: str) -> bool:
        nonlocal health_checks
        health_checks += 1
        return health_checks > 1

    async def stop(_account_id: str, *, forget: bool = False) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr(inbox_runtime, "subscribe_incoming_messages", subscribe)
    monkeypatch.setattr(inbox_runtime, "ensure_incoming_message_connection", ensure)
    monkeypatch.setattr(inbox_runtime, "stop_incoming_messages", stop)
    await inbox_runtime.reconcile_inboxes_on_startup()
    await _wait_for(lambda: len(subscriptions) == 2 and health_checks >= 2)
    await inbox_runtime.shutdown_inbox_runtime()

    assert subscriptions == ["active", "active"]


async def _accounts(items: list[SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(accounts=items)


async def _wait_for(predicate: Callable[[], bool]) -> None:
    deadline = asyncio.get_running_loop().time() + 1
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError
        await asyncio.sleep(0)
