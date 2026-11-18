"""Who is never asked (design 5.5 rules 1-2), in BOTH paths of the master switch.

The rollback switch (``SPAM_INTEL__ENABLED=false``) hands the call to the pre-gateway funnel;
it must not hand it an account the gateway would never ask: no row, a session that is not
alive, or no proxy (D1). ``TELEGRAM__ALLOW_DIRECT_CONNECTION`` only decides whether ``core``
would connect such an account, and the guard must hold with it on as well as off.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import upsert_spam_status
from schemas.spam_status import SpamStatusVerdict
from services import spam_intel

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from tests.services.spam_intel.conftest import Clock, Events, FakeProbe

    MakeAccount = Callable[..., Awaitable[None]]

_DEAD = ("unauthorized", "session_error", "account_error", "frozen")
_TRANSIENT = ("flood_wait", "proxy_error", "network_error", "unknown_error")


@pytest.fixture(params=[True, False], ids=["gateway-on", "gateway-off"])
def switch(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> bool:
    """Both paths, with the lower level's direct-connection refusal OFF (the dangerous mix)."""
    monkeypatch.setattr(settings.spam_intel, "enabled", request.param)
    monkeypatch.setattr(settings.telegram, "allow_direct_connection", True)
    return bool(request.param)


@pytest.mark.asyncio
@pytest.mark.usefixtures("switch")
@pytest.mark.parametrize("forced", [True, False])
async def test_an_account_without_a_proxy_is_never_asked_whatever_the_switches_say(
    make_account: MakeAccount, probe: FakeProbe, *, forced: bool
) -> None:
    await make_account("acc-1", proxy=None)
    verdict = await spam_intel.ask("acc-1", force=forced)
    assert (verdict.status, verdict.served_from, verdict.reason) == (
        "unknown",
        "refused",
        "no_proxy",
    )
    assert probe.calls == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("switch")
async def test_a_missing_account_is_refused_with_its_reason_in_both_paths(
    probe: FakeProbe,
) -> None:
    verdict = await spam_intel.ask("ghost", force=True)
    assert (verdict.served_from, verdict.reason, verdict.detail) == (
        "refused",
        "account_missing",
        "account not found",
    )
    assert probe.calls == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("switch")
@pytest.mark.parametrize("status", _DEAD)
async def test_a_dead_session_is_refused_and_never_asked_in_either_path(
    make_account: MakeAccount, probe: FakeProbe, status: str
) -> None:
    await make_account("acc-1", status=status)
    verdict = await spam_intel.ask("acc-1", force=True)
    assert (verdict.served_from, verdict.reason) == ("refused", "account_not_alive")
    assert probe.calls == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("switch")
@pytest.mark.parametrize("status", _TRANSIENT)
async def test_a_transient_status_is_deferred_not_refused_in_either_path(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock, status: str
) -> None:
    await make_account("acc-1", status=status)
    verdict = await spam_intel.ask("acc-1", force=True)
    assert (verdict.status, verdict.served_from, verdict.reason) == (
        "unknown",
        "deferred",
        "account_status_transient",
    )
    expected = clock.now() + timedelta(minutes=settings.spam_intel.account_status_retry_minutes)
    assert spam_intel.parse_retry(verdict) == expected
    assert probe.calls == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("switch")
async def test_the_operator_is_held_by_a_transient_status_with_the_reason_visible(
    make_account: MakeAccount, probe: FakeProbe
) -> None:
    await make_account("acc-1", status="flood_wait")
    verdict = await spam_intel.operator_check("acc-1", confirm_out_of_turn=True)
    assert (verdict.served_from, verdict.reason) == ("deferred", "account_status_transient")
    assert verdict.retry_at is not None
    assert probe.calls == []


@pytest.mark.asyncio
async def test_a_transient_account_is_asked_again_once_the_wait_is_over(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock
) -> None:
    from core.db import _get_engine  # noqa: PLC0415

    await make_account("acc-1", status="proxy_error")
    assert (await spam_intel.ask("acc-1", force=True)).served_from == "deferred"
    with _get_engine().begin() as connection:  # the session check cleared it
        connection.exec_driver_sql(
            "UPDATE accounts SET status = 'alive' WHERE account_id = 'acc-1'"
        )
    clock.advance(minutes=settings.spam_intel.account_status_retry_minutes + 1)
    assert (await spam_intel.ask("acc-1", force=True)).served_from == "probe"
    assert probe.calls == ["acc-1"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("switch")
@pytest.mark.parametrize("status", ["new", "alive"])
async def test_a_new_or_alive_account_with_a_proxy_is_asked(
    make_account: MakeAccount, probe: FakeProbe, status: str
) -> None:
    await make_account("acc-1", status=status)
    verdict = await spam_intel.ask("acc-1", force=True)
    assert verdict.status == "clean"
    assert probe.calls == ["acc-1"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("switch")
async def test_every_refusal_writes_its_event(make_account: MakeAccount, events: Events) -> None:
    await make_account("no-proxy", proxy=None)
    await make_account("dead", status="frozen")
    await make_account("flooded", status="flood_wait")
    for account in ("ghost", "no-proxy", "dead", "flooded"):
        await spam_intel.ask(account, force=True)
    refused = events.of("spam_intel_probe_refused")
    assert sorted((str(r["action"]), str(r["reason"])) for r in refused) == [
        ("defer", "account_status_transient"),
        ("refuse", "account_missing"),
        ("refuse", "account_not_alive"),
        ("refuse", "no_proxy"),
    ]


@pytest.mark.asyncio
async def test_with_the_gateway_off_a_proxyless_account_still_gets_a_fresh_stored_reading(
    make_account: MakeAccount,
    probe: FakeProbe,
    clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard stops a QUESTION; handing back what is already stored asks nothing."""
    monkeypatch.setattr(settings.spam_intel, "enabled", False)
    await make_account("acc-1", proxy=None)
    await upsert_spam_status(
        SpamStatusVerdict(
            account_id="acc-1",
            status="limited",
            checked_at=(clock.now() - timedelta(hours=1)).isoformat(),
        )
    )
    verdict = await spam_intel.ask("acc-1")
    assert verdict.status == "limited"
    assert probe.calls == []
    forced = await spam_intel.ask("acc-1", force=True)
    assert (forced.served_from, forced.reason) == ("refused", "no_proxy")
    assert probe.calls == []
