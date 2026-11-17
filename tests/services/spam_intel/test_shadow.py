"""The shadow comparison: the fold's word before each real reading, against what @SpamBot said."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import upsert_spam_status
from core.repositories.logs import insert_log_row
from schemas.logs import LogEventInput
from schemas.spam_status import SpamStatusVerdict
from services import spam_intel
from tests.services.spam_intel.conftest import CLEAN_TEXT, LIMITED_TEXT

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from tests.services.spam_intel.conftest import Clock, Events, FakeProbe

    MakeAccount = Callable[..., Awaitable[None]]

_EVENT = "spam_intel_shadow_comparison"


@pytest.mark.asyncio
async def test_a_real_reading_logs_the_fold_before_it_and_the_real_status(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock, events: Events
) -> None:
    await make_account("a1")
    await spam_intel.record_evidence(
        "a1", "spambot_clean", source="spambot", observed_at=clock.now() - timedelta(hours=40)
    )
    probe.reply = CLEAN_TEXT
    await spam_intel.ask("a1", force=True, caller="warming.quarantine")
    assert events.of(_EVENT) == [
        {"fold": "clean", "real": "clean", "agree": True, "primary_kind": "spambot_clean"}
    ]


@pytest.mark.asyncio
async def test_a_disagreement_is_logged_with_the_kind_the_fold_stood_on(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock, events: Events
) -> None:
    await make_account("a1")
    await spam_intel.record_evidence(
        "a1", "peer_flood_send", source="execute", observed_at=clock.now() - timedelta(hours=1)
    )
    probe.reply = CLEAN_TEXT
    await spam_intel.ask("a1", force=True, caller="warming.quarantine")
    assert events.of(_EVENT) == [
        {"fold": "limited", "real": "clean", "agree": False, "primary_kind": "peer_flood_send"}
    ]


@pytest.mark.asyncio
async def test_an_account_the_fold_knows_nothing_about_reads_unknown_and_disagrees(
    make_account: MakeAccount, probe: FakeProbe, events: Events
) -> None:
    await make_account("a1")
    probe.reply = LIMITED_TEXT
    await spam_intel.ask("a1", caller="warming.cycle")
    assert events.of(_EVENT) == [
        {"fold": "unknown", "real": "limited", "agree": False, "primary_kind": None}
    ]


@pytest.mark.asyncio
async def test_the_event_carries_only_enumerated_values_never_text(
    make_account: MakeAccount, probe: FakeProbe, events: Events
) -> None:
    await make_account("a1")
    probe.reply = LIMITED_TEXT
    await spam_intel.ask("a1", caller="warming.cycle")
    (extra,) = events.of(_EVENT)
    assert set(extra) == {"fold", "real", "agree", "primary_kind"}
    assert LIMITED_TEXT not in str(extra)


@pytest.mark.asyncio
async def test_a_probe_that_failed_has_no_real_answer_to_compare(
    make_account: MakeAccount, probe: FakeProbe, events: Events
) -> None:
    await make_account("a1")
    probe.error = "ConnectionError"
    await spam_intel.ask("a1", caller="warming.cycle")
    assert events.of(_EVENT) == []


@pytest.mark.asyncio
async def test_a_served_answer_is_not_a_reading_and_logs_nothing(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock, events: Events
) -> None:
    await make_account("a1")
    await upsert_spam_status(
        SpamStatusVerdict(account_id="a1", status="clean", checked_at=clock.now().isoformat())
    )
    await spam_intel.ask("a1", caller="warming.cycle")
    assert (probe.calls, events.of(_EVENT)) == ([], [])


@pytest.mark.asyncio
async def test_with_passive_decisions_on_the_fold_is_no_longer_a_shadow(
    make_account: MakeAccount,
    probe: FakeProbe,
    events: Events,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.spam_intel, "passive_decisions", True)
    await make_account("a1")
    await spam_intel.ask("a1", caller="warming.cycle")
    assert (probe.calls, events.of(_EVENT)) == (["a1"], [])


@pytest.mark.asyncio
async def test_the_master_switch_off_compares_nothing(
    make_account: MakeAccount,
    probe: FakeProbe,
    events: Events,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.spam_intel, "enabled", False)
    await make_account("a1")
    await spam_intel.ask("a1", caller="warming.cycle")
    assert (probe.calls, events.of(_EVENT)) == (["a1"], [])


@pytest.mark.asyncio
async def test_a_failing_fold_read_never_fails_the_probe(
    make_account: MakeAccount,
    probe: FakeProbe,
    events: Events,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.spam_intel import _shadow  # noqa: PLC0415 - only this test needs it

    async def _boom(_account_id: str) -> None:
        msg = "db gone"
        raise RuntimeError(msg)

    monkeypatch.setattr(_shadow, "current_view", _boom)
    await make_account("a1")
    verdict = await spam_intel.ask("a1", caller="warming.cycle")
    assert (verdict.served_from, probe.calls, events.of(_EVENT)) == ("probe", ["a1"], [])


async def _row(account: str, fold: str, real: str, *, agree: bool) -> None:
    await insert_log_row(
        LogEventInput(
            level="INFO",
            event=_EVENT,
            account_id=account,
            extra={"fold": fold, "real": real, "agree": agree, "primary_kind": None},
        )
    )


@pytest.mark.asyncio
async def test_the_agreement_counts_come_from_the_logged_comparisons(
    make_account: MakeAccount,
) -> None:
    await make_account("a1")
    await _row("a1", "clean", "clean", agree=True)
    await _row("a1", "clean", "clean", agree=True)
    await _row("a1", "limited", "clean", agree=False)
    await insert_log_row(LogEventInput(level="INFO", event="spam_intel_probe", account_id="a1"))

    counts = await spam_intel.shadow_agreement()

    assert (counts.total, counts.agree) == (3, 2)
    assert counts.rows == {"clean->clean": 2, "limited->clean": 1}


@pytest.mark.asyncio
async def test_no_comparisons_yet_is_zero_not_an_error() -> None:
    counts = await spam_intel.shadow_agreement()
    assert (counts.total, counts.agree, counts.rows) == (0, 0, {})
