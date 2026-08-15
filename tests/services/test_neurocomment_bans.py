"""Tests for ``services.neurocomment.bans`` — the live per-channel ban check.

Seeds real campaign/account/channel rows and patches BOTH Telegram seams this check
reaches, so no network is touched: the participant probe (``_seams.execute_read``) and
the @SpamBot verdict (``_seams.refresh_spam_status``), which a ``restricted`` probe
pulls in through the ban-confirmation ladder. Stubbing only the first left the second
live — with ``TELEGRAM__API_ID`` / ``TELEGRAM__API_HASH`` in the environment four of
these tests opened a real Telegram connection, and CI, which has no credentials, never
saw it because Telethon refuses to construct a client without them. Asserts the
per-channel aggregation and pin-aware account resolution.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

import pytest

from core.config import settings
from core.db import (
    assign_account_to_campaign,
    configure_database,
    create_account,
    create_campaign,
    fetch_readiness,
    link_channel_to_campaign,
    mark_pair_banned,
    upsert_readiness,
)
from core.logging import reset_logging_for_tests, setup_logging
from core.repositories.neurocomment import set_campaign_account_channels
from core.telegram_client import TelegramReadError
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate
from schemas.spam_status import SpamStatusVerdict
from schemas.telegram_actions import BanCheckResult, CheckBannedInChannel
from services.neurocomment import _seams
from services.neurocomment.bans import check_campaign_channel_bans

if TYPE_CHECKING:
    from pathlib import Path

_State = Literal["can_send", "restricted", "not_member", "comments_disabled"]


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    reset_logging_for_tests()
    setup_logging()


def _patch_seam(
    monkeypatch: pytest.MonkeyPatch, mapping: dict[tuple[str, str], _State | Exception]
) -> None:
    """Route each (account_id, channel) probe to a state string or a raised error.

    Both Telegram seams, because both are reached: a ``restricted`` probe runs the
    ban-confirmation ladder, and that asks @SpamBot. No test here asserts on the
    verdict — they assert the aggregated per-channel status, which ``_aggregate``
    reads off the probe states alone — so the stub answers ``unknown``, the status
    that says nobody reached @SpamBot. It is also the answer the live call was
    already degrading to, so the ladder still stops short of the sticky ban and the
    group leave, and no test's outcome moves.
    """

    async def fake_execute_read(account_id: str, action: CheckBannedInChannel) -> BanCheckResult:
        value = mapping.get((account_id, action.channel), "can_send")
        if isinstance(value, Exception):
            raise value
        return BanCheckResult(state=value)

    async def fake_refresh_spam_status(
        account_id: str,
        *,
        force: bool = False,  # noqa: ARG001 - the seam's signature; the stub answers the same either way.
    ) -> SpamStatusVerdict:
        return SpamStatusVerdict(
            account_id=account_id, status="unknown", checked_at=datetime.now(UTC).isoformat()
        )

    monkeypatch.setattr(_seams, "execute_read", fake_execute_read)
    monkeypatch.setattr(_seams, "refresh_spam_status", fake_refresh_spam_status)


async def _seed(channels: list[str], accounts: list[str]) -> str:
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    for acc in accounts:
        await create_account(AccountCreate(account_id=acc, label=acc))
        await assign_account_to_campaign(campaign.campaign_id, acc)
    for channel in channels:
        await link_channel_to_campaign(campaign.campaign_id, channel)
    return campaign.campaign_id


def _status_of(items: list, channel: str) -> str:
    return next(item.status for item in items if item.channel == channel)


@pytest.mark.asyncio
async def test_unknown_campaign_returns_none() -> None:
    assert await check_campaign_channel_bans("nope") is None


@pytest.mark.asyncio
async def test_can_send_is_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    cid = await _seed(["@a"], ["acc-1"])
    _patch_seam(monkeypatch, {("acc-1", "@a"): "can_send"})

    result = await check_campaign_channel_bans(cid)

    assert result is not None
    assert _status_of(result.items, "@a") == "ok"


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["restricted", "not_member"])
async def test_blocked_states_are_banned(monkeypatch: pytest.MonkeyPatch, state: _State) -> None:
    cid = await _seed(["@a"], ["acc-1"])
    _patch_seam(monkeypatch, {("acc-1", "@a"): state})

    result = await check_campaign_channel_bans(cid)

    assert result is not None
    assert _status_of(result.items, "@a") == "banned"


@pytest.mark.asyncio
async def test_any_can_send_wins_over_a_ban(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two unpinned accounts serve @a; one banned, one fine → channel still ok."""
    cid = await _seed(["@a"], ["acc-1", "acc-2"])
    _patch_seam(monkeypatch, {("acc-1", "@a"): "restricted", ("acc-2", "@a"): "can_send"})

    result = await check_campaign_channel_bans(cid)

    assert result is not None
    assert _status_of(result.items, "@a") == "ok"


@pytest.mark.asyncio
async def test_probe_error_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    cid = await _seed(["@a"], ["acc-1"])
    _patch_seam(monkeypatch, {("acc-1", "@a"): TelegramReadError("flood")})

    result = await check_campaign_channel_bans(cid)

    assert result is not None
    assert _status_of(result.items, "@a") == "unknown"


@pytest.mark.asyncio
async def test_can_send_probe_does_not_lift_a_ban(monkeypatch: pytest.MonkeyPatch) -> None:
    """A per-pair ban is permanent — 'Проверить каналы' reports, it does not un-ban.

    The button used to lift the ban on a live ``can_send`` verdict, defended as
    unreachable ("once we leave, this pair can only ever probe as not_member"). The leave
    is best-effort and has its own failing-leave test, so the pair could stay a member and
    probe ``can_send`` — and then a hint that says the channel is closed to this account
    for good would be a lie the operator could disprove by pressing a button.
    """
    cid = await _seed(["@a"], ["acc-1"])
    await upsert_readiness("acc-1", "@a", joined=True, captcha_passed=True, ready=True)
    await mark_pair_banned("acc-1", "@a")
    _patch_seam(monkeypatch, {("acc-1", "@a"): "can_send"})

    result = await check_campaign_channel_bans(cid)

    assert result is not None
    # The channel is reachable (some account can write there) — but the banned pair stays
    # banned and stays out of selection.
    assert _status_of(result.items, "@a") == "ok"
    readiness = await fetch_readiness("acc-1", "@a")
    assert readiness is not None
    assert readiness.banned is True
    assert readiness.ready is False


@pytest.mark.asyncio
async def test_still_banned_probe_keeps_the_ban(monkeypatch: pytest.MonkeyPatch) -> None:
    """A restricted probe keeps the ban — and nothing else ever lifts it either."""
    cid = await _seed(["@a"], ["acc-1"])
    await upsert_readiness("acc-1", "@a", joined=True, captcha_passed=True, ready=True)
    await mark_pair_banned("acc-1", "@a")
    _patch_seam(monkeypatch, {("acc-1", "@a"): "restricted"})

    result = await check_campaign_channel_bans(cid)

    assert result is not None
    assert _status_of(result.items, "@a") == "banned"
    readiness = await fetch_readiness("acc-1", "@a")
    assert readiness is not None
    assert readiness.banned is True


@pytest.mark.asyncio
async def test_pin_scopes_accounts_and_unpinned_channel_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """acc-1 pinned to @a → serves only @a; @b has no serving account → unknown."""
    cid = await _seed(["@a", "@b"], ["acc-1"])
    await set_campaign_account_channels(cid, "acc-1", ["@a"])
    _patch_seam(monkeypatch, {("acc-1", "@a"): "restricted"})

    result = await check_campaign_channel_bans(cid)

    assert result is not None
    assert _status_of(result.items, "@a") == "banned"
    assert _status_of(result.items, "@b") == "unknown"
