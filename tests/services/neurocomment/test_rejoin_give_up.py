"""What happens to ONE pair that runs out of re-joins while its channel lives on.

The channel drop needs every serving account to be finished, so an account that gave up
on a chat the others comment in fine produced no log line at all, left nothing, and kept
the channel's green «Готов» on its board row. These tests pin the line, the leave, the
once-only guarantee, and — the point of the whole rule — that the account keeps working
everywhere else.

Own module: ``test_rejoin`` is at 700-line test cap territory already.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.db import (  # type: ignore[attr-defined]
    _get_engine,
    assign_account_to_campaign,
    create_account,
    create_campaign,
    deactivate_channel,
    fetch_active_campaign_for_channel,
    fetch_readiness,
    link_channel_to_campaign,
    list_campaign_channels,
    list_recent_logs,
    stamp_rejoin_attempt,
    upsert_readiness,
)
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate
from schemas.telegram_actions import LeaveDiscussionGroup
from services.neurocomment import _give_up, _rejoin, _runtime, _seams, engine, onboarding
from services.neurocomment.settings_store import load_settings as load_neuro_settings
from tests.services.neurocomment.engine_support import _Readiness
from tests.services.neurocomment.onboarding_support import _JoinStub, _ReadStub

pytestmark = pytest.mark.usefixtures("isolate_onboarding")

_CHANNEL = "@chan"
_OTHER = "@other"


async def _campaign(*accounts: str, channels: tuple[str, ...] = (_CHANNEL,)) -> str:
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    for channel in channels:
        await link_channel_to_campaign(campaign.campaign_id, channel)
    for account_id in accounts:
        await create_account(AccountCreate(account_id=account_id, session_name=account_id))
        await assign_account_to_campaign(campaign.campaign_id, account_id)
    return campaign.campaign_id


async def _spend_the_budget(account_id: str, channel: str = _CHANNEL) -> None:
    """Park the pair with both attempts spent and answered, the last window elapsed.

    Each stamp is followed by the re-park a failed re-join writes: that later readiness
    write is what tells the rule the attempt was answered rather than still owed.
    """
    await upsert_readiness(account_id, channel, joined=False, captcha_passed=True, ready=False)
    for _ in range(2):
        await stamp_rejoin_attempt(account_id, channel)
        await upsert_readiness(account_id, channel, joined=False, captcha_passed=True, ready=False)
    _backdate(account_id, channel, hours=25)


def _backdate(
    account_id: str,
    channel: str,
    *,
    hours: float,
    checked_hours: float | None = None,
) -> None:
    """Age the pair's last stamp, and optionally the readiness write that answered it.

    Both matter, and they mean different things: the stamp's age is the re-join window,
    while the stamp being NEWER than ``checked_at`` is what "this attempt was never
    answered by a pass" is read off.
    """
    stamp = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    with _get_engine().begin() as connection:
        connection.exec_driver_sql(
            "UPDATE neurocomment_readiness SET rejoin_attempted_at = ? "
            "WHERE account_id = ? AND channel = ?",
            (stamp, account_id, channel),
        )
        if checked_hours is not None:
            connection.exec_driver_sql(
                "UPDATE neurocomment_readiness SET checked_at = ? "
                "WHERE account_id = ? AND channel = ?",
                (
                    (datetime.now(UTC) - timedelta(hours=checked_hours)).isoformat(),
                    account_id,
                    channel,
                ),
            )


def _patch_telegram(monkeypatch: pytest.MonkeyPatch) -> _JoinStub:
    read = _ReadStub(linked_chat_id=4423, comments_enabled=True)
    join = _JoinStub()
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)
    monkeypatch.setattr(_runtime, "_ensure_onboarding_running", lambda *_: None)
    return join


async def _give_up_lines() -> list[dict[str, object]]:
    return [
        entry.extra
        for entry in reversed(await list_recent_logs(limit=50))
        if entry.event == "neurocomment_rejoin_gave_up"
    ]


def _leaves(join: _JoinStub) -> list[str]:
    return [account for account, a in join.calls if isinstance(a, LeaveDiscussionGroup)]


@pytest.mark.asyncio
async def test_a_pair_out_of_attempts_leaves_the_chat_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One line, one leave — the trace this pair never had while its channel lived on."""
    campaign_id = await _campaign("acc-1", "acc-2")
    await _spend_the_budget("acc-1")
    # acc-2 comments here fine, so the channel is not going anywhere.
    await upsert_readiness("acc-2", _CHANNEL, joined=True, captcha_passed=True, ready=True)
    join = _patch_telegram(monkeypatch)

    await _rejoin.review_access_lost(datetime.now(UTC))

    assert _leaves(join) == ["acc-1"]
    lines = await _give_up_lines()
    assert [line["channel"] for line in lines] == [_CHANNEL]
    assert lines[0]["reason"] == "2/2"
    links = (await list_campaign_channels(campaign_id)).links
    assert any(link.channel == _CHANNEL and link.active for link in links)


@pytest.mark.asyncio
async def test_the_next_sweep_tick_repeats_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """The review runs every five minutes; without the mark it would re-log forever."""
    await _campaign("acc-1", "acc-2")
    await _spend_the_budget("acc-1")
    await upsert_readiness("acc-2", _CHANNEL, joined=True, captcha_passed=True, ready=True)
    join = _patch_telegram(monkeypatch)

    await _rejoin.review_access_lost(datetime.now(UTC))
    await _rejoin.review_access_lost(datetime.now(UTC))
    await _rejoin.review_access_lost(datetime.now(UTC))

    assert len(await _give_up_lines()) == 1
    assert len(_leaves(join)) == 1
    row = await fetch_readiness("acc-1", _CHANNEL)
    assert row is not None
    assert row.rejoin_gave_up is True


@pytest.mark.asyncio
async def test_the_account_keeps_commenting_on_its_other_channels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole risk of leaving a chat: an account that stops working everywhere.

    Readiness is per (account, channel) and the engine selects on that channel's row
    alone, so the pair that gave up here must not cost the account a post there.
    """
    campaign_id = await _campaign("acc-1", channels=(_CHANNEL, _OTHER))
    await _spend_the_budget("acc-1")
    await upsert_readiness("acc-1", _OTHER, joined=True, captcha_passed=True, ready=True)
    _patch_telegram(monkeypatch)
    # The health gate reads warming signals this fixture does not build; selection's own
    # readiness filter — the thing under test — is the line below it.
    monkeypatch.setattr(engine, "evaluate_readiness", lambda *_a, **_k: _Readiness(ready=True))

    await _rejoin.review_access_lost(datetime.now(UTC))

    assert len(await _give_up_lines()) == 1
    campaign = await fetch_active_campaign_for_channel(_OTHER)
    assert campaign is not None
    selection = await engine._select_account(campaign, _OTHER, await load_neuro_settings())
    assert selection.account_id == "acc-1"
    links = (await list_campaign_channels(campaign_id)).links
    assert any(link.channel == _OTHER and link.active for link in links)


@pytest.mark.asyncio
async def test_a_pair_still_owed_an_attempt_is_left_where_it_is(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The attempt is charged BEFORE the pass runs, and the pass may never reach the pair.

    Its commonest reason is the account's rolling-24h join cap, which is shared across
    every channel it serves — so "2/2" here can mean "another channel used up the day's
    joins", and leaving a chat nobody knocked on would be the rule's worst mistake.
    """
    await _campaign("acc-1")
    await upsert_readiness("acc-1", _CHANNEL, joined=False, captcha_passed=True, ready=False)
    for _ in range(2):
        await stamp_rejoin_attempt("acc-1", _CHANNEL)
    # Both attempts stamped and their window elapsed, but no pass ever wrote the row
    # after them — the shape a pair takes while its account sits at the join cap.
    _backdate("acc-1", _CHANNEL, hours=25, checked_hours=30)
    join = _patch_telegram(monkeypatch)

    await _rejoin.review_access_lost(datetime.now(UTC))

    assert await _give_up_lines() == []
    assert _leaves(join) == []


@pytest.mark.asyncio
async def test_a_dead_address_is_the_channel_rules_business_not_this_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A terminal verdict spends no attempt, and the leave would fail on the same address."""
    await _campaign("acc-1")
    await upsert_readiness(
        "acc-1",
        _CHANNEL,
        joined=False,
        captcha_passed=True,
        ready=False,
        access_lost_reason="UsernameNotOccupiedError",
    )
    join = _patch_telegram(monkeypatch)

    await _rejoin.review_access_lost(datetime.now(UTC))

    assert await _give_up_lines() == []
    assert _leaves(join) == []


@pytest.mark.asyncio
async def test_a_failed_leave_still_leaves_the_verdict_standing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The account is usually out of the chat already — that is what the budget proved."""
    await _campaign("acc-1", "acc-2")
    await _spend_the_budget("acc-1")
    await upsert_readiness("acc-2", _CHANNEL, joined=True, captcha_passed=True, ready=True)
    join = _patch_telegram(monkeypatch)
    join.set(_CHANNEL, status="failed", error_type="UserNotParticipantError")

    await _rejoin.review_access_lost(datetime.now(UTC))

    lines = await _give_up_lines()
    assert [line["leave"] for line in lines] == ["failed"]
    row = await fetch_readiness("acc-1", _CHANNEL)
    assert row is not None
    assert row.rejoin_gave_up is True


@pytest.mark.asyncio
async def test_a_real_re_join_clears_the_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    """The badge and the budget go together: a pair back in the chat starts over clean.

    Through the rule and onboarding, not the repository call underneath them — the write
    is only worth anything if the path that re-joins a pair actually reaches it. The way
    there for a finished pair is a stamp gone stale: the budget it spent stops counting,
    the review buys it a fresh attempt, and the pass it pokes is what re-joins.
    """
    await _campaign("acc-1", "acc-2")
    await _spend_the_budget("acc-1")
    await upsert_readiness("acc-2", _CHANNEL, joined=True, captcha_passed=True, ready=True)
    _patch_telegram(monkeypatch)
    await _rejoin.review_access_lost(datetime.now(UTC))
    _backdate("acc-1", _CHANNEL, hours=49)  # two windows: the spent budget goes stale

    await _rejoin.review_access_lost(datetime.now(UTC))
    await onboarding.onboard_account_channel("acc-1", _CHANNEL)

    row = await fetch_readiness("acc-1", _CHANNEL)
    assert row is not None
    assert (row.ready, row.rejoin_gave_up, row.rejoin_attempts) == (True, False, 0)


@pytest.mark.asyncio
async def test_re_linking_the_channel_takes_the_badge_off_with_the_counter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other place a timeline restarts — and the one the log line tells them to use.

    Left set, the mark also filters the pair out of the rule for good: a second spent
    budget on the fresh timeline would never be reported or left again.
    """
    campaign_id = await _campaign("acc-1", "acc-2")
    await _spend_the_budget("acc-1")
    await upsert_readiness("acc-2", _CHANNEL, joined=True, captcha_passed=True, ready=True)
    _patch_telegram(monkeypatch)
    await _rejoin.review_access_lost(datetime.now(UTC))

    await deactivate_channel(campaign_id, _CHANNEL)
    await link_channel_to_campaign(campaign_id, _CHANNEL)

    row = await fetch_readiness("acc-1", _CHANNEL)
    assert row is not None
    assert (row.rejoin_gave_up, row.rejoin_attempts) == (False, 0)


@pytest.mark.asyncio
async def test_a_pair_that_got_back_in_mid_tick_is_neither_marked_nor_walked_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The review decides off a snapshot; onboarding may re-join a pair while it runs.

    An unconditional mark would badge a working pair and leave the chat it had just
    re-entered, and nothing would ever clear either.
    """
    await _campaign("acc-1", "acc-2")
    await _spend_the_budget("acc-1")
    await upsert_readiness("acc-2", _CHANNEL, joined=True, captcha_passed=True, ready=True)
    join = _patch_telegram(monkeypatch)
    stale = (await _rejoin.list_access_lost_readiness()).readiness
    # ...and the pair is back in the chat before the rule acts on that snapshot.
    await upsert_readiness("acc-1", _CHANNEL, joined=True, captcha_passed=True, ready=True)

    await _give_up.report(_CHANNEL, [row for row in stale if row.account_id == "acc-1"])

    assert await _give_up_lines() == []
    assert _leaves(join) == []
    row = await fetch_readiness("acc-1", _CHANNEL)
    assert row is not None
    assert row.rejoin_gave_up is False
