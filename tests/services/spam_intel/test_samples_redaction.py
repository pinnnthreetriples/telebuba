"""Reply samples keep no personal data: every leak class the audit found, and the age limit."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import _get_engine
from core.repositories.spam_reply_samples import list_reply_samples
from schemas.spam_status import SpamBotHistory
from services import spam_intel
from services._spam_parse import find_date
from services._spam_redact import redact_reply
from services.spam_intel import _history

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from tests.services.spam_intel.conftest import Clock, FakeProbe

    MakeAccount = Callable[..., Awaitable[None]]

_MAX = 1000


def _redact(text: str, *names: str | None) -> str:
    return redact_reply(text, names, _MAX)


@pytest.mark.parametrize(
    "phone",
    [
        "+34 612 345 678",
        "+34-612-345-678",
        "+1 (415) 555-2671",
        "(415) 555-2671",
        "415.555.2671",
        "415 555 2671",
        "612345678",
        "06 12 34 56 78",
        "06-12-34-56-78",
        "+7 999 123-45-67",
        "8 (999) 123 45 67",
    ],
)
def test_a_phone_in_any_common_format_is_masked(phone: str) -> None:
    redacted = _redact(f"Hola, tu número {phone} no tiene restricciones.")
    digits = "".join(ch for ch in phone if ch.isdigit())
    assert not any(ch.isdigit() for ch in redacted), redacted
    assert digits[-4:] not in redacted


def test_a_date_survives_but_the_digits_beside_it_do_not() -> None:
    redacted = _redact("Limited until 12.07.2026 11:44 UTC. Ref 2026-07-12. Call 415-555-2671.")
    assert find_date(redacted) == "12.07.2026"
    assert "2026-07-12" in redacted
    assert "2671" not in redacted


@pytest.mark.parametrize(
    "text",
    [
        "Write to @some_stranger for help.",
        "Contact @abc about it.",
        "See https://t.me/some_stranger and tell them.",
        "See t.me/joinchat/AAAAAEyz and tell them.",
        "Open tg://resolve?domain=some_stranger now.",
        "Mail me: a.b+c@example.com",
    ],
)
def test_a_username_link_or_address_of_anyone_is_masked(text: str) -> None:
    redacted = _redact(text)
    for leaked in ("some_stranger", "abc", "AAAAAEyz", "example.com"):
        assert leaked not in redacted, redacted


def test_the_bots_own_name_is_not_personal() -> None:
    assert "@SpamBot" in _redact("Ask @SpamBot again.")


@pytest.mark.parametrize("name", ["A", "Al", "Li", "Ann"])
def test_a_short_name_of_the_account_is_masked_as_a_whole_token(name: str) -> None:
    redacted = _redact(f"Hi {name}, your account is fine. Thanks, {name}!", name)
    assert f"Hi {name}," not in redacted
    assert f"{name}!" not in redacted
    assert "<name>" in redacted


def test_a_short_name_does_not_mangle_the_words_around_it() -> None:
    redacted = _redact("A limit applies to Al's account, not to alarm.", "Al")
    assert "alarm" in redacted
    assert "Al's" not in redacted


def test_each_word_of_a_composite_name_is_masked() -> None:
    redacted = _redact("Dear Ivan, goodbye Petrov-Sidorov.", "Ivan Petrov-Sidorov")
    assert "Ivan" not in redacted
    assert "Petrov" not in redacted


def test_a_long_digit_run_is_masked_wherever_it_stands() -> None:
    redacted = _redact("Your id 123456789 and 98765 are noted")
    assert "123456789" not in redacted
    assert "98765" not in redacted


def test_the_redacted_text_is_bounded() -> None:
    assert len(redact_reply("word " * 500, [], 100)) <= 100


def _age_days(days: float) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


@pytest.mark.asyncio
async def test_a_sample_stored_through_the_probe_has_no_personal_data(
    make_account: MakeAccount, probe: FakeProbe
) -> None:
    await make_account("acc-1")
    with _get_engine().begin() as connection:
        connection.exec_driver_sql(
            "UPDATE accounts SET first_name = 'M', phone = '+34612345678'"
            " WHERE account_id = 'acc-1'"
        )
    probe.reply = "Hola M, escribe a @someone_else o al +34 612-345-678. Todo bien."
    await spam_intel.ask("acc-1", force=True)
    (sample,) = await list_reply_samples()
    assert "someone_else" not in sample.sample
    assert "345" not in sample.sample
    assert "Hola M," not in sample.sample


@pytest.mark.asyncio
async def test_samples_older_than_the_retention_are_pruned(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock
) -> None:
    await make_account("acc-1")
    probe.reply = "An old distinct reply of the bot."
    await spam_intel.ask("acc-1", force=True)
    stale = settings.spam_intel.sample_max_age_days + 1
    with _get_engine().begin() as connection:
        connection.exec_driver_sql(
            "UPDATE spam_reply_samples SET last_seen_at = ?", (_age_days(stale),)
        )
    clock.advance(hours=7)
    probe.reply = "A new distinct reply of the bot."
    await spam_intel.ask("acc-1", force=True)
    texts = [sample.sample for sample in await list_reply_samples()]
    assert texts == ["A new distinct reply of the bot."]


@pytest.mark.asyncio
async def test_capture_off_still_ages_out_what_was_stored_before(
    make_account: MakeAccount,
    probe: FakeProbe,
    clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await make_account("acc-1")
    probe.reply = "A stored reply of the bot."
    await spam_intel.ask("acc-1", force=True)
    with _get_engine().begin() as connection:
        connection.exec_driver_sql(
            "UPDATE spam_reply_samples SET last_seen_at = ?", (_age_days(400),)
        )
    monkeypatch.setattr(settings.spam_intel, "sample_capture", False)
    clock.advance(hours=7)
    await spam_intel.ask("acc-1", force=True)
    assert await list_reply_samples() == []


@pytest.mark.asyncio
async def test_the_history_sweep_stores_only_redacted_samples(
    make_account: MakeAccount, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The history sweep stores redacted samples whatever ``SAMPLE_CAPTURE`` says.

    That switch governs the probe; the sweep is its own explicit opt-in to learn the texts.
    """
    await make_account("acc-1")

    async def _read(account_id: str, _limit: int) -> SpamBotHistory:
        return SpamBotHistory(
            account_id=account_id, texts=["Hola, llama al 415-555-2671 o escribe a @another_one."]
        )

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(_history, "read_spambot_history", _read)
    monkeypatch.setattr(_history, "_sleep", _no_sleep)
    monkeypatch.setattr(settings.spam_intel, "sample_capture", False)
    await _history.run_history_sweep()
    (sample,) = await list_reply_samples()
    assert (sample.source, "2671" in sample.sample, "another_one" in sample.sample) == (
        "history",
        False,
        False,
    )
