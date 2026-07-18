"""Tests for ``services.spam_status`` — probe parsing and TTL caching."""

from __future__ import annotations

import asyncio as _asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import configure_database, create_account, get_spam_status
from core.logging import reset_logging_for_tests, setup_logging
from schemas.accounts import AccountCreate
from schemas.spam_status import SpamStatusProbe, SpamStatusVerdict
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


@pytest.mark.parametrize(
    ("reply", "expected_detail"),
    [
        (
            "Your account is limited until review; final restriction until 12 July 2026.",
            "until review; final restriction until 12 July 2026",
        ),
        (
            " until 12 July 2026, this account remains restricted.",
            "until 12 July 2026, this account remains restricted",
        ),
        ("Votre compte reste bloqué jusqu'au 12 juillet 2026!", "12 juillet 2026"),
        ("Your account is restricted until 2026-07-12.", "until 2026-07-12"),
    ],
)
def test_classify_limited_extracts_first_expiry_without_punctuation(
    reply: str,
    expected_detail: str,
) -> None:
    probe = SpamStatusProbe(account_id="a", reply_text=reply)
    assert spam_status.classify_spam_probe(probe) == ("limited", expected_detail)


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


def test_classify_unmatched_reply_defaults_to_clean_via_heuristic() -> None:
    """Heuristic: any non-empty reply without a date or "automat" stem → clean.

    The probe ran, the bot answered, the answer doesn't carry the structural
    signals of a limit or review — better to call it clean than to bail to
    "вердикт не получен" on a locale we don't have explicit keywords for.
    """
    probe = SpamStatusProbe(account_id="a", reply_text="hello there")
    assert spam_status.classify_spam_probe(probe) == ("clean", None)


def test_classify_empty_reply_is_unknown_with_no_detail() -> None:
    """A genuinely empty reply has no signal to surface — keep detail=None."""
    probe = SpamStatusProbe(account_id="a", reply_text="")
    assert spam_status.classify_spam_probe(probe) == ("unknown", None)


def test_classify_clean_russian_long_form() -> None:
    """@SpamBot Russian long form — clean verdict via «хорошие новости» / «не применены»."""
    probe = SpamStatusProbe(
        account_id="a",
        reply_text=(
            "Хорошие новости, к вашему аккаунту в настоящий момент "
            "не применены никакие ограничения."
        ),
    )
    assert spam_status.classify_spam_probe(probe) == ("clean", None)


def test_classify_clean_russian_short_form() -> None:
    """@SpamBot Russian short form — must NOT trip the limited path on «ограничений».

    Regression: «Ваш аккаунт свободен от каких-либо ограничений» contains the
    substring «ограничен», which used to live in ``_LIMITED_MARKERS`` and
    flipped the verdict to ``limited``. The "свободен от" clean marker is the
    fix — it has to match before any limited check sees the shared stem.
    """
    probe = SpamStatusProbe(
        account_id="a",
        reply_text="Ваш аккаунт свободен от каких-либо ограничений.",
    )
    assert spam_status.classify_spam_probe(probe) == ("clean", None)


def test_classify_limited_russian() -> None:
    """@SpamBot Russian — limited verdict via the discriminative «наложены» stem.

    «ограничен» alone is NOT enough: it appears in clean replies too. The
    bot's limited replies always include «наложены» (imposed) which is
    unambiguous.
    """
    probe = SpamStatusProbe(
        account_id="a",
        reply_text="К сожалению, на ваш аккаунт сейчас наложены ограничения.",
    )
    status, _ = spam_status.classify_spam_probe(probe)
    assert status == "limited"


def test_classify_being_checked_russian() -> None:
    """@SpamBot in Russian — automated review."""
    probe = SpamStatusProbe(
        account_id="a",
        reply_text="Ваш аккаунт сейчас проверяется нашей автоматизированной системой.",
    )
    assert spam_status.classify_spam_probe(probe) == ("unknown", "account is being checked")


# --- language-agnostic heuristic --------------------------------------------


def test_classify_clean_german_via_heuristic() -> None:
    """German clean reply has no keyword match, but no date / no automat → clean."""
    probe = SpamStatusProbe(
        account_id="a",
        reply_text=(
            "Gute Nachrichten! Deinem Konto sind momentan keine Einschränkungen "
            "auferlegt. Du bist so frei wie ein Vogel – genieße es!"
        ),
    )
    assert spam_status.classify_spam_probe(probe) == ("clean", None)


def test_classify_clean_spanish_via_heuristic() -> None:
    """Spanish clean reply lands in the heuristic and is correctly classified."""
    probe = SpamStatusProbe(
        account_id="a",
        reply_text="Buenas noticias, no hay restricciones en tu cuenta.",
    )
    assert spam_status.classify_spam_probe(probe) == ("clean", None)


def test_classify_limited_german_via_date_heuristic() -> None:
    """A date in the reply is treated as a limit verdict, regardless of language."""
    probe = SpamStatusProbe(
        account_id="a",
        reply_text="Dein Konto ist bis 12. Juli 2026 eingeschränkt.",
    )
    status, detail = spam_status.classify_spam_probe(probe)
    assert status == "limited"
    assert detail is not None
    assert "2026" in detail


def test_classify_limited_french_via_date_heuristic() -> None:
    """French limit reply (no English keyword stem) classified via the date."""
    probe = SpamStatusProbe(
        account_id="a",
        reply_text="Votre compte est limité jusqu'au 12 juillet 2026.",
    )
    status, detail = spam_status.classify_spam_probe(probe)
    assert status == "limited"
    assert detail is not None
    assert "juillet" in detail or "2026" in detail


def test_classify_limited_iso_date_heuristic() -> None:
    """ISO-format date (2026-07-12) is also detected."""
    probe = SpamStatusProbe(
        account_id="a",
        reply_text="Conta limitada até 2026-07-12.",
    )
    status, detail = spam_status.classify_spam_probe(probe)
    assert status == "limited"
    assert detail is not None
    assert "2026-07-12" in detail


def test_classify_being_checked_spanish_via_automat_stem() -> None:
    """The "automat" stem covers en/de/fr/it/pt/es; here Spanish "automatizado"."""
    probe = SpamStatusProbe(
        account_id="a",
        reply_text="Tu cuenta está siendo verificada por nuestro sistema automatizado.",
    )
    assert spam_status.classify_spam_probe(probe) == ("unknown", "account is being checked")


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
    assert verdict.detail == "until 2026-08-01"


class FrozenDatetime(datetime):
    current = datetime(2026, 7, 18, 12, tzinfo=UTC)

    @classmethod
    def now(cls, tz: object = None) -> datetime:  # noqa: ARG003
        return cls.current


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ttl_hours", "checked_at", "expected_calls"),
    [
        (0.0, FrozenDatetime.current + timedelta(minutes=1), 1),
        (1.0, FrozenDatetime.current - timedelta(minutes=30), 0),
        (1.0, "2026-07-18T11:30:00", 0),
        (1.0, FrozenDatetime.current - timedelta(hours=1), 1),
    ],
)
async def test_refresh_cache_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    ttl_hours: float,
    checked_at: datetime | str,
    expected_calls: int,
) -> None:
    monkeypatch.setattr(settings.warming, "spam_status_ttl_hours", ttl_hours)
    monkeypatch.setattr(spam_status, "datetime", FrozenDatetime)
    stamp = checked_at.isoformat() if isinstance(checked_at, datetime) else checked_at
    cached = SpamStatusVerdict(
        account_id=f"cache-{ttl_hours}-{stamp}",
        status="clean",
        checked_at=stamp,
    )
    calls = 0

    async def get_cached(_account_id: str) -> SpamStatusVerdict:
        return cached

    async def probe(account_id: str) -> SpamStatusProbe:
        nonlocal calls
        calls += 1
        return SpamStatusProbe(account_id=account_id, reply_text="Good news, no limits.")

    async def save(verdict: SpamStatusVerdict) -> SpamStatusVerdict:
        return verdict

    async def log(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(spam_status, "get_spam_status", get_cached)
    monkeypatch.setattr(spam_status, "check_spam_status", probe)
    monkeypatch.setattr(spam_status, "upsert_spam_status", save)
    monkeypatch.setattr(spam_status, "log_event", log)

    result = await spam_status.refresh_spam_status(cached.account_id)

    assert calls == expected_calls
    assert result.status == "clean"


@pytest.mark.asyncio
async def test_refresh_concurrent_callers_share_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two cycles waking together must produce a single @SpamBot probe, not two."""
    monkeypatch.setattr(settings.warming, "spam_status_ttl_hours", 24.0)
    monkeypatch.setattr(spam_status, "_REFRESH_LOCKS", {})
    await create_account(AccountCreate(account_id="acc-1"))

    calls = 0
    started = _asyncio.Event()
    probe_event = _asyncio.Event()

    async def slow_probe(account_id: str) -> SpamStatusProbe:
        nonlocal calls
        calls += 1
        started.set()
        await _asyncio.wait_for(probe_event.wait(), timeout=2.0)
        return SpamStatusProbe(
            account_id=account_id,
            reply_text="Good news, no limits are currently applied to your account.",
        )

    monkeypatch.setattr(spam_status, "check_spam_status", slow_probe)

    task1 = _asyncio.create_task(spam_status.refresh_spam_status("acc-1"))
    tasks = [task1]
    try:
        await _asyncio.wait_for(started.wait(), timeout=2.0)
        task2 = _asyncio.create_task(spam_status.refresh_spam_status("acc-1"))
        tasks.append(task2)
        await _asyncio.sleep(0)
    finally:
        probe_event.set()
        await _asyncio.wait_for(_asyncio.gather(*tasks), timeout=2.0)

    assert calls == 1


@pytest.mark.asyncio
async def test_refreshes_for_different_accounts_do_not_block_each_other(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_started = _asyncio.Event()
    second_started = _asyncio.Event()
    release_first = _asyncio.Event()

    async def no_cache(_account_id: str) -> None:
        return None

    async def probe(account_id: str) -> SpamStatusProbe:
        if account_id == "independent-a":
            first_started.set()
            await _asyncio.wait_for(release_first.wait(), timeout=2.0)
        else:
            second_started.set()
        return SpamStatusProbe(account_id=account_id, reply_text="Good news, no limits.")

    async def save(verdict: SpamStatusVerdict) -> SpamStatusVerdict:
        return verdict

    async def log(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(spam_status, "get_spam_status", no_cache)
    monkeypatch.setattr(spam_status, "check_spam_status", probe)
    monkeypatch.setattr(spam_status, "upsert_spam_status", save)
    monkeypatch.setattr(spam_status, "log_event", log)
    first = _asyncio.create_task(spam_status.refresh_spam_status("independent-a"))
    tasks = [first]
    independent = False
    try:
        await _asyncio.wait_for(first_started.wait(), timeout=2.0)
        second = _asyncio.create_task(spam_status.refresh_spam_status("independent-b"))
        tasks.append(second)
        await _asyncio.wait_for(second_started.wait(), timeout=1.0)
        independent = True
    except TimeoutError:
        independent = False
    finally:
        release_first.set()
        await _asyncio.wait_for(_asyncio.gather(*tasks), timeout=2.0)
    assert independent, "one account's slow probe must not block another account"
