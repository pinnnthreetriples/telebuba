"""Isolation and fakes for the spam-intel gateway tests: a clock, a probe, an event recorder."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import _get_engine, create_account
from core.repositories.proxies import assign_account_to_proxy, create_proxy
from schemas.accounts import AccountCreate
from schemas.proxy import ProxyCreate
from schemas.spam_status import SpamStatusProbe
from services import _account_owner, spam_status
from services.spam_intel import _answers, _evidence, _gateway, _history

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator

CLEAN_TEXT = "Good news, no limits are currently applied to your account."
LIMITED_TEXT = "Your account is now limited until 29 Oct 2026."


@dataclass
class Clock:
    """The gateway's clock; tests move it."""

    at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def now(self) -> datetime:
        return self.at

    def advance(self, **delta: float) -> None:
        self.at += timedelta(**delta)


class FakeRandom:
    """The lower bound of every range: no jitter, so the arithmetic is readable."""

    def uniform(self, low: float, _high: float) -> float:
        return low


@dataclass
class FakeProbe:
    """Stands in for ``check_spam_status``: counts calls, answers from a script."""

    reply: str | None = CLEAN_TEXT
    error: str | None = None
    delay: float = 0.0
    calls: list[str] = field(default_factory=list)

    async def __call__(self, account_id: str) -> SpamStatusProbe:
        import asyncio  # noqa: PLC0415 - only the delaying tests need it

        self.calls.append(account_id)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            return SpamStatusProbe(account_id=account_id, error=self.error)
        return SpamStatusProbe(account_id=account_id, reply_text=self.reply)


@dataclass
class Events:
    """Every ``log_event`` the gateway and the executor wrote."""

    rows: list[tuple[str, str, dict[str, object]]] = field(default_factory=list)

    def names(self) -> list[str]:
        return [name for _level, name, _extra in self.rows]

    def of(self, name: str) -> list[dict[str, object]]:
        return [extra for _level, event, extra in self.rows if event == name]


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    fake = Clock()
    monkeypatch.setattr(_gateway, "now", fake.now)
    monkeypatch.setattr(_evidence, "now", fake.now)
    return fake


@pytest.fixture(autouse=True)
def _gateway_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Age the process, drop in-flight state, and turn the spacing off unless a test wants it."""
    _gateway.reset_for_tests()
    monkeypatch.setattr(_gateway, "rng", FakeRandom())
    intel = settings.spam_intel
    monkeypatch.setattr(intel, "fleet_gap_min_seconds", 0.0)
    monkeypatch.setattr(intel, "fleet_gap_max_seconds", 0.0)
    monkeypatch.setattr(intel, "proxy_spacing_minutes", 0.0)
    monkeypatch.setattr(intel, "history_startup_delay_minutes", 0.0)
    yield
    _gateway.reset_for_tests()


@pytest.fixture(autouse=True)
def events(monkeypatch: pytest.MonkeyPatch) -> Events:
    recorded = Events()

    async def _record(
        level: str,
        event: str,
        *,
        account_id: str | None = None,  # noqa: ARG001
        extra: dict[str, object] | None = None,
    ) -> None:
        recorded.rows.append((level, event, dict(extra or {})))

    for module in (_gateway, _answers, spam_status, _history, _evidence):
        monkeypatch.setattr(module, "log_event", _record)
    return recorded


@pytest.fixture(autouse=True)
def probe(monkeypatch: pytest.MonkeyPatch) -> FakeProbe:
    fake = FakeProbe()
    monkeypatch.setattr(spam_status, "check_spam_status", fake)
    return fake


@pytest.fixture
def make_account() -> Callable[..., Awaitable[None]]:
    """Account builder: its own proxy by default, a named ``proxy`` is shared by the accounts."""
    counter = {"n": 0}
    proxies: dict[str, str] = {}

    async def _make(
        account_id: str,
        *,
        proxy: str | None = "own",
        status: str | None = None,
    ) -> None:
        await create_account(AccountCreate(account_id=account_id))
        if proxy is not None:
            key = account_id if proxy == "own" else proxy
            if key not in proxies:
                counter["n"] += 1
                created = await create_proxy(
                    ProxyCreate(proxy_type="socks5", host=f"p{counter['n']}.example.com", port=1080)
                )
                proxies[key] = created.id
            await assign_account_to_proxy(proxies[key], account_id)
        if status is not None:
            with _get_engine().begin() as connection:
                connection.exec_driver_sql(
                    "UPDATE accounts SET status = ? WHERE account_id = ?", (status, account_id)
                )

    return _make


@pytest.fixture(autouse=True)
def _owner_state() -> Iterator[None]:
    _account_owner.reset_for_tests()
    yield
    _account_owner.reset_for_tests()
