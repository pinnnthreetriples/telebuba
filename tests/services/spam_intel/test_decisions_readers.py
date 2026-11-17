"""The readers behind the switch: off = exactly as before, on = the fold decides.

Outreach protection, the masstagging fleet, trust and the accounts table each read the stored
status of an account with a dated limit that ended three days ago, or of one that only has
PeerFlood evidence. Off, they see what they always saw; on, an ended limit reads ``unknown`` and a
fresh PeerFlood blocks.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import upsert_spam_status
from schemas.outreach_protection import OutreachProtection
from schemas.spam_status import SpamStatusVerdict
from services import spam_intel
from services.accounts import list_accounts_page
from services.accounts._outreach_protection import skipped_accounts
from services.masstagging._fleet import fleet_of
from services.trust import account_trust_score

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from tests.services.spam_intel.conftest import Clock

    MakeAccount = Callable[..., Awaitable[None]]


async def _ended_limit(account: str, clock: Clock) -> None:
    day = (clock.now() - timedelta(days=3)).date()
    await upsert_spam_status(
        SpamStatusVerdict(
            account_id=account,
            status="limited",
            detail=f"until {day.isoformat()}",
            checked_at=(clock.now() - timedelta(days=4)).isoformat(),
        )
    )


def _switch(monkeypatch: pytest.MonkeyPatch, *, decisions: bool) -> None:
    monkeypatch.setattr(settings.spam_intel, "passive_decisions", decisions)


async def _skipped(account: str) -> list[str]:
    result = await skipped_accounts([account], OutreachProtection(), datetime.now(UTC))
    return [item.reason for item in result]


@pytest.mark.asyncio
@pytest.mark.parametrize(("decisions", "expected"), [(False, ["account_spam_blocked"]), (True, [])])
async def test_outreach_protection_and_an_ended_limit(
    make_account: MakeAccount,
    clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
    *,
    decisions: bool,
    expected: list[str],
) -> None:
    await make_account("a1")
    await _ended_limit("a1", clock)
    _switch(monkeypatch, decisions=decisions)
    assert await _skipped("a1") == expected


@pytest.mark.asyncio
async def test_outreach_protection_skips_a_passive_peer_flood_only_when_the_fold_decides(
    make_account: MakeAccount, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_account("a1")
    await spam_intel.record_evidence(
        "a1", "peer_flood_send", source="execute", observed_at=clock.now() - timedelta(hours=1)
    )
    _switch(monkeypatch, decisions=False)
    assert await _skipped("a1") == []
    _switch(monkeypatch, decisions=True)
    assert await _skipped("a1") == ["account_spam_blocked"]


@pytest.mark.asyncio
async def test_outreach_protection_with_skip_injured_off_reads_nothing(
    make_account: MakeAccount, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_account("a1")
    await _ended_limit("a1", clock)
    _switch(monkeypatch, decisions=True)
    off = OutreachProtection(skip_injured=False, rest_after_limit=True)
    assert await skipped_accounts(["a1"], off, datetime.now(UTC)) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("decisions", "blocked"), [(False, True), (True, False)])
async def test_the_masstagging_fleet_and_an_ended_limit(
    make_account: MakeAccount,
    clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
    *,
    decisions: bool,
    blocked: bool,
) -> None:
    await make_account("a1")
    await _ended_limit("a1", clock)
    _switch(monkeypatch, decisions=decisions)
    assert (await fleet_of(["a1"]))["a1"].spamblock is blocked


@pytest.mark.asyncio
async def test_the_masstagging_fleet_drops_an_account_with_fresh_peer_flood_when_on(
    make_account: MakeAccount, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_account("a1")
    await spam_intel.record_evidence(
        "a1", "peer_flood_send", source="execute", observed_at=clock.now() - timedelta(hours=1)
    )
    _switch(monkeypatch, decisions=False)
    assert (await fleet_of(["a1"]))["a1"].spamblock is False
    _switch(monkeypatch, decisions=True)
    assert (await fleet_of(["a1"]))["a1"].spamblock is True


@pytest.mark.asyncio
@pytest.mark.parametrize(("decisions", "docked"), [(False, True), (True, False)])
async def test_trust_docks_an_ended_limit_only_when_the_switch_is_off(
    make_account: MakeAccount,
    clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
    *,
    decisions: bool,
    docked: bool,
) -> None:
    await make_account("a1")
    await _ended_limit("a1", clock)
    _switch(monkeypatch, decisions=decisions)
    assert ("spam-limited" in (await account_trust_score("a1")).reasons) is docked


@pytest.mark.asyncio
async def test_trust_docks_fresh_peer_flood_when_the_fold_decides(
    make_account: MakeAccount, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_account("a1")
    await spam_intel.record_evidence(
        "a1", "peer_flood_send", source="execute", observed_at=clock.now() - timedelta(hours=1)
    )
    _switch(monkeypatch, decisions=False)
    assert "spam-limited" not in (await account_trust_score("a1")).reasons
    _switch(monkeypatch, decisions=True)
    assert "spam-limited" in (await account_trust_score("a1")).reasons


@pytest.mark.asyncio
@pytest.mark.parametrize(("decisions", "shown"), [(False, "limited"), (True, "unknown")])
async def test_the_accounts_table_shows_an_ended_limit_as_unknown_when_on(
    make_account: MakeAccount,
    clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
    *,
    decisions: bool,
    shown: str,
) -> None:
    await make_account("a1")
    await _ended_limit("a1", clock)
    _switch(monkeypatch, decisions=decisions)
    page = await list_accounts_page()
    (row,) = [a for a in page.items if a.account_id == "a1"]
    assert row.spam_status == shown
