"""A fake phone for the telephony dispatcher: its screen, what it typed, what holds it."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from services.telephony import _dispatcher, clear_dispatcher_state, clear_sessions

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Iterator

    from core.emulator.ui_dump import Screen, UiNode


@dataclass
class FakePhone:
    """What the dispatcher sees of the phone, and what it did to it."""

    screen: Screen = "code"
    activity: str | None = None
    fail_with: BaseException | None = None
    # When set, typing waits for it: a code "still being typed".
    gate: asyncio.Event | None = None
    account_phone: str | None = "+79991234567"
    typed: list[tuple[str, str]] = field(default_factory=list)
    pauses: list[tuple[float, float]] = field(default_factory=list)
    events: list[tuple[str, dict[str, object]]] = field(default_factory=list)


@pytest.fixture(autouse=True)
def _reset_telephony() -> Iterator[None]:
    clear_sessions()
    clear_dispatcher_state()
    yield
    clear_sessions()
    clear_dispatcher_state()


@pytest.fixture
def phone(monkeypatch: pytest.MonkeyPatch) -> FakePhone:
    fake = FakePhone()

    async def resolve_serial(phone_id: str) -> str:
        return f"serial-{phone_id}"

    async def read_screen(_serial: str) -> tuple[Screen, list[UiNode]]:
        return fake.screen, []

    async def enter_code(serial: str, _nodes: list[UiNode], code: str) -> None:
        if fake.gate is not None:
            await fake.gate.wait()
        if fake.fail_with is not None:
            raise fake.fail_with
        fake.typed.append((serial, code))

    def uniform(low: float, high: float) -> float:
        fake.pauses.append((low, high))
        return 0.0

    async def log_event(
        _level: str,
        event: str,
        account_id: str | None = None,
        extra: dict[str, object] | None = None,
    ) -> None:
        del account_id
        fake.events.append((event, extra or {}))

    async def require_account(account_id: str) -> SimpleNamespace:
        return SimpleNamespace(account_id=account_id, phone=fake.account_phone)

    monkeypatch.setattr(_dispatcher, "_resolve_serial", resolve_serial)
    monkeypatch.setattr(_dispatcher.telegram_app, "read_screen", read_screen)
    monkeypatch.setattr(_dispatcher.telegram_app, "enter_code", enter_code)
    monkeypatch.setattr(_dispatcher, "phone_activity", lambda _account_id: fake.activity)
    monkeypatch.setattr(_dispatcher._RNG, "uniform", uniform)
    monkeypatch.setattr(_dispatcher, "log_event", log_event)
    monkeypatch.setattr(_dispatcher, "require_account", require_account)
    return fake
