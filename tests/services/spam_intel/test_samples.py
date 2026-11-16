"""Reply samples and the shadow comparison, kept by the probe executor."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import _get_engine, create_account
from core.repositories.spam_reply_samples import list_reply_samples
from schemas.accounts import AccountCreate
from services import spam_intel

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from tests.services.spam_intel.conftest import Clock, Events, FakeProbe

    MakeAccount = Callable[..., Awaitable[None]]

# A made-up Spanish reply (the fleet speaks Spanish; the real texts are not known yet). It has
# no en/ru marker and no date, which is exactly the shape the legacy parser reads as clean.
SYNTHETIC_ES = "Hola María, tu cuenta no tiene restricciones. Tu número 34612345678 está bien."


@pytest.mark.asyncio
async def test_a_probe_keeps_a_redacted_sample_with_both_classifications(probe: FakeProbe) -> None:
    await create_account(AccountCreate(account_id="acc-1", phone="34612345678"))
    with _get_engine().begin() as connection:
        connection.exec_driver_sql(
            "UPDATE accounts SET first_name = 'María', proxy_id = NULL WHERE account_id = 'acc-1'"
        )
    await _give_proxy()
    probe.reply = SYNTHETIC_ES
    await spam_intel.ask("acc-1", force=True)
    samples = await list_reply_samples()
    assert len(samples) == 1
    sample = samples[0]
    assert "María" not in sample.sample
    assert "34612345678" not in sample.sample
    assert (sample.legacy_status, sample.strict_status, sample.source) == (
        "clean",
        "unknown",
        "probe",
    )
    assert sample.seen_count == 1


async def _give_proxy() -> None:
    """Put ``acc-1`` behind a proxy without re-creating it."""
    from core.repositories.proxies import assign_account_to_proxy, create_proxy  # noqa: PLC0415
    from schemas.proxy import ProxyCreate  # noqa: PLC0415

    created = await create_proxy(ProxyCreate(proxy_type="socks5", host="s.example.com", port=1080))
    await assign_account_to_proxy(created.id, "acc-1")


@pytest.mark.asyncio
async def test_the_same_text_from_two_accounts_is_one_row_with_a_count(
    make_account: MakeAccount, clock: Clock
) -> None:
    await make_account("acc-1")
    await make_account("acc-2")
    for account in ("acc-1", "acc-2"):
        await spam_intel.ask(account, force=True)
        clock.advance(minutes=5)
    samples = await list_reply_samples()
    assert [(s.seen_count) for s in samples] == [2]


@pytest.mark.asyncio
async def test_the_samples_table_stays_bounded(
    make_account: MakeAccount,
    probe: FakeProbe,
    clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.spam_intel, "samples_max_rows", 2)
    await make_account("acc-1")
    for index in range(4):
        probe.reply = f"Distinct reply number {index} from the bot."
        await spam_intel.ask("acc-1", force=True)
        clock.advance(hours=1)
    assert len(await list_reply_samples()) == 2


@pytest.mark.asyncio
async def test_capture_off_keeps_nothing(
    make_account: MakeAccount,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.spam_intel, "sample_capture", False)
    await make_account("acc-1")
    await spam_intel.ask("acc-1", force=True)
    assert await list_reply_samples() == []


@pytest.mark.asyncio
async def test_a_failed_probe_or_an_empty_reply_keeps_no_sample(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock
) -> None:
    await make_account("acc-1")
    probe.error = "TimeoutError"
    await spam_intel.ask("acc-1", force=True)
    clock.advance(hours=7)
    probe.error = None
    probe.reply = "   "
    await spam_intel.ask("acc-1", force=True)
    assert await list_reply_samples() == []


@pytest.mark.asyncio
async def test_a_sample_that_cannot_be_stored_never_fails_the_probe(
    make_account: MakeAccount,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services import spam_status  # noqa: PLC0415

    async def _boom(*_args: object, **_kwargs: object) -> None:
        msg = "table is gone"
        raise RuntimeError(msg)

    monkeypatch.setattr(spam_status, "upsert_reply_sample", _boom)
    await make_account("acc-1")
    verdict = await spam_intel.ask("acc-1", force=True)
    assert (verdict.status, verdict.served_from) == ("clean", "probe")


@pytest.mark.asyncio
async def test_the_shadow_log_names_a_disagreement_without_quoting_the_reply(
    make_account: MakeAccount, probe: FakeProbe, events: Events
) -> None:
    await make_account("acc-1")
    probe.reply = SYNTHETIC_ES
    await spam_intel.ask("acc-1", force=True)
    rows = events.of("spam_intel_parser_shadow_disagreement")
    assert rows == [{"legacy": "clean", "strict": "unknown", "strict_detail": "unrecognized_reply"}]
    assert "María" not in repr(rows)


@pytest.mark.asyncio
async def test_no_shadow_row_when_the_classifiers_agree_or_the_shadow_is_off(
    make_account: MakeAccount,
    probe: FakeProbe,
    clock: Clock,
    events: Events,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await make_account("acc-1")
    await spam_intel.ask("acc-1", force=True)  # a known clean text: both agree
    assert events.of("spam_intel_parser_shadow_disagreement") == []
    clock.advance(hours=1)
    monkeypatch.setattr(settings.spam_intel, "parser_shadow", False)
    probe.reply = SYNTHETIC_ES
    await spam_intel.ask("acc-1", force=True)
    assert events.of("spam_intel_parser_shadow_disagreement") == []


@pytest.mark.asyncio
async def test_the_strict_switch_stops_the_shadow_and_changes_the_verdict(
    make_account: MakeAccount,
    probe: FakeProbe,
    events: Events,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.spam_intel, "strict_parser", True)
    await make_account("acc-1")
    probe.reply = SYNTHETIC_ES
    verdict = await spam_intel.ask("acc-1", force=True)
    assert verdict.status == "unknown"
    assert events.of("spam_intel_parser_shadow_disagreement") == []
