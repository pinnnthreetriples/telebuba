"""Tests for ``services.spam_status`` — probe parsing and TTL caching."""

from __future__ import annotations

import asyncio as _asyncio
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import configure_database, create_account, get_spam_status
from core.logging import reset_logging_for_tests, setup_logging
from schemas.accounts import AccountCreate
from schemas.spam_status import SpamStatusProbe
from services import spam_status

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    reset_logging_for_tests()
    setup_logging()
    yield
    reset_logging_for_tests()


def test_classify_clean() -> None:
    probe = SpamStatusProbe(
        account_id="a",
        reply_text="Good news, no limits are currently applied to your account.",
    )
    assert spam_status.classify_spam_probe(probe) == ("clean", None)


def test_classify_limited_extracts_until() -> None:
    probe = SpamStatusProbe(
        account_id="a",
        reply_text="Your account is now limited until 12 July 2026.",
    )
    status, detail = spam_status.classify_spam_probe(probe)
    assert status == "limited"
    assert detail is not None
    assert "until 12 July 2026" in detail


def test_classify_being_checked_is_unknown() -> None:
    probe = SpamStatusProbe(account_id="a", reply_text="Your account is being checked.")
    assert spam_status.classify_spam_probe(probe) == ("unknown", "account is being checked")


def test_classify_error_is_unknown() -> None:
    probe = SpamStatusProbe(account_id="a", error="TimeoutError: boom")
    assert spam_status.classify_spam_probe(probe) == ("unknown", "TimeoutError: boom")


def test_classify_falls_back_to_restriction_flag() -> None:
    probe = SpamStatusProbe(
        account_id="a",
        reply_text="something unrelated",
        restricted=True,
        restriction_reason="terms violation",
    )
    assert spam_status.classify_spam_probe(probe) == ("limited", "terms violation")


def test_classify_ambiguous_is_unknown() -> None:
    probe = SpamStatusProbe(account_id="a", reply_text="hello there")
    assert spam_status.classify_spam_probe(probe) == ("unknown", None)


@pytest.mark.asyncio
async def test_refresh_probes_then_serves_from_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    await create_account(AccountCreate(account_id="acc-1"))
    calls: list[str] = []

    async def fake_probe(account_id: str) -> SpamStatusProbe:
        calls.append(account_id)
        return SpamStatusProbe(account_id=account_id, reply_text="Good news, no limits.")

    monkeypatch.setattr(spam_status, "check_spam_status", fake_probe)

    first = await spam_status.refresh_spam_status("acc-1")
    assert first.status == "clean"
    assert len(calls) == 1

    # Fresh cache → no second probe.
    second = await spam_status.refresh_spam_status("acc-1")
    assert second.status == "clean"
    assert len(calls) == 1

    # force bypasses the cache.
    await spam_status.refresh_spam_status("acc-1", force=True)
    assert len(calls) == 2

    persisted = await get_spam_status("acc-1")
    assert persisted is not None
    assert persisted.status == "clean"


@pytest.mark.asyncio
async def test_refresh_reprobes_when_ttl_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "spam_status_ttl_hours", 0.0)
    await create_account(AccountCreate(account_id="acc-1"))
    calls: list[str] = []

    async def fake_probe(account_id: str) -> SpamStatusProbe:
        calls.append(account_id)
        return SpamStatusProbe(account_id=account_id, reply_text="now limited until 2026-08-01.")

    monkeypatch.setattr(spam_status, "check_spam_status", fake_probe)

    await spam_status.refresh_spam_status("acc-1")
    verdict = await spam_status.refresh_spam_status("acc-1")
    assert len(calls) == 2
    assert verdict.status == "limited"


@pytest.mark.asyncio
async def test_refresh_concurrent_callers_share_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two cycles waking together must produce a single @SpamBot probe, not two."""
    monkeypatch.setattr(settings.warming, "spam_status_ttl_hours", 24.0)
    monkeypatch.setattr(spam_status, "_REFRESH_LOCKS", {})
    await create_account(AccountCreate(account_id="acc-1"))

    calls = 0
    probe_event = _asyncio.Event()

    async def slow_probe(account_id: str) -> SpamStatusProbe:
        nonlocal calls
        calls += 1
        # Hold the first probe until the second caller is already waiting.
        await probe_event.wait()
        return SpamStatusProbe(
            account_id=account_id,
            reply_text="Good news, no limits are currently applied to your account.",
        )

    monkeypatch.setattr(spam_status, "check_spam_status", slow_probe)

    task1 = _asyncio.create_task(spam_status.refresh_spam_status("acc-1"))
    await _asyncio.sleep(0)
    task2 = _asyncio.create_task(spam_status.refresh_spam_status("acc-1"))
    await _asyncio.sleep(0)
    probe_event.set()
    await task1
    await task2

    assert calls == 1
