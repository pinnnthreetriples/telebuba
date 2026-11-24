"""Where the key repair runs: first in every guard pass, and before an operator's retry."""

from __future__ import annotations

import pytest

from schemas.accounts import AccountRead
from schemas.devices import SecondDeviceResult
from services import session_guard
from services.accounts import devices
from services.session_guard import _loop


@pytest.fixture(autouse=True)
def _fresh() -> None:
    _loop.reset_for_tests()


@pytest.mark.asyncio
async def test_the_guard_repairs_interrupted_key_flows_before_timing_any_old_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []

    def _step(name: str, answer: object = 0):
        async def _run() -> object:
            order.append(name)
            return answer

        return _run

    monkeypatch.setattr(_loop, "repair_key_intents", _step("repair"))
    monkeypatch.setattr(_loop, "close_due_old_keys", _step("old_keys"))
    monkeypatch.setattr(_loop, "run_watch_pass", _step("watch", None))
    monkeypatch.setattr(_loop, "run_backup_pass", _step("backups", None))
    monkeypatch.setattr(_loop, "run_vault_health_check", _step("vault", None))
    monkeypatch.setattr(_loop, "run_file_reconciliation", _step("files", None))

    await session_guard.run_guard_pass()

    assert order[:2] == ["repair", "old_keys"]


@pytest.mark.asyncio
async def test_a_failing_repair_step_does_not_stop_the_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ran: list[str] = []

    async def _broken() -> int:
        raise RuntimeError

    async def _due() -> int:
        ran.append("old_keys")
        return 2

    async def _none() -> None:
        return None

    monkeypatch.setattr(_loop, "repair_key_intents", _broken)
    monkeypatch.setattr(_loop, "close_due_old_keys", _due)
    for name in ("run_watch_pass", "run_backup_pass", "run_vault_health_check"):
        monkeypatch.setattr(_loop, name, _none)
    monkeypatch.setattr(_loop, "run_file_reconciliation", _none)

    assert await session_guard.run_guard_pass() == 2
    assert ran == ["old_keys"]


@pytest.mark.asyncio
async def test_a_second_device_retry_heals_the_account_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    account = AccountRead.model_validate(
        {
            "account_id": "acc",
            "session_name": "acc",
            "status": "alive",
            "created_at": "2026-10-31T00:00:00+00:00",
            "updated_at": "2026-10-31T00:00:00+00:00",
            "devices": ["mobile"],
        }
    )

    async def _fetch(_account_id: str) -> AccountRead:
        return account

    async def _repair(account_id: str | None = None) -> int:
        order.append(f"repair:{account_id}")
        return 0

    async def _authorize(request: object) -> SecondDeviceResult:
        order.append("authorize")
        return SecondDeviceResult(
            account_id="acc",
            device_type=request.device_type,  # ty: ignore[unresolved-attribute]
            code=None,
        )

    monkeypatch.setattr(devices, "fetch_account", _fetch)
    monkeypatch.setattr(devices, "repair_key_intents", _repair)
    monkeypatch.setattr(devices, "authorize_second_device", _authorize)

    await devices.sign_in_account_device("acc", "desktop")

    assert order == ["repair:acc", "authorize"]
