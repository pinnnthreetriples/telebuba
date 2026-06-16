"""Tests for ``services.content`` — normalisation, filtering and dedup."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import configure_database
from core.logging import reset_logging_for_tests, setup_logging
from services import content

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


def test_normalize_collapses_case_and_punctuation() -> None:
    assert content.normalize_text("Hello,  World!!") == "hello world"
    assert content.content_hash("Hello, World!") == content.content_hash("hello world")


def test_has_link() -> None:
    assert content.has_link("see https://example.com")
    assert content.has_link("join t.me/foo")
    assert not content.has_link("just a normal sentence")


def test_has_forbidden_word() -> None:
    assert content.has_forbidden_word("хочешь купить дёшево?", ["купить"])
    assert not content.has_forbidden_word("привет, как дела", ["купить"])


def test_is_acceptable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "content_block_links", True)
    monkeypatch.setattr(settings.warming, "content_forbidden_words", ["реклама"])
    assert content.is_acceptable("привет, как сам?")
    assert not content.is_acceptable("это реклама канала")
    assert not content.is_acceptable("посмотри https://spam.example")


@pytest.mark.asyncio
async def test_is_duplicate_after_register(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "content_dedup_window_days", 7.0)
    assert not await content.is_duplicate("hi there")
    await content.register_sent("hi there")
    assert await content.is_duplicate("hi there")
    # normalisation means punctuation/case variants are the same content
    assert await content.is_duplicate("Hi, there!")


@pytest.mark.asyncio
async def test_is_duplicate_disabled_with_zero_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "content_dedup_window_days", 0.0)
    await content.register_sent("hi there")
    assert not await content.is_duplicate("hi there")


@pytest.mark.asyncio
async def test_try_reserve_sent_first_wins_second_loses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "content_dedup_window_days", 7)
    assert await content.try_reserve_sent("hi there") is True
    assert await content.try_reserve_sent("hi there") is False
    # Normalised collision: punctuation/case-different but same hash → also loses.
    assert await content.try_reserve_sent("Hi, there!") is False


@pytest.mark.asyncio
async def test_try_reserve_sent_zero_window_always_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "content_dedup_window_days", 0)
    assert await content.try_reserve_sent("hi") is True
    assert await content.try_reserve_sent("hi") is True
