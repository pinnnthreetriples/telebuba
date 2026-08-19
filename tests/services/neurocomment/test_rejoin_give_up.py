"""What happens to ONE pair that runs out of re-joins while its channel lives on.

The channel drop needs every serving account to be finished, so an account that gave up
on a chat the others comment in fine produced no log line at all and kept the channel's
green «Готов» on its board row. These tests pin the line, the once-only guarantee, the
finality of the verdict and the two ways out of it, and — the point of the whole rule —
that the account keeps working everywhere else.

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
    mark_nc_handed_off,
    mark_promoted_to_nc,
    stamp_rejoin_attempt,
    upsert_readiness,
)
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate
from services.neurocomment import (
    _gates,
    _give_up,
    _rejoin,
    _runtime,
    _seams,
    board,
    campaigns,
    engine,
    onboarding,
)
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


@pytest.mark.asyncio
async def test_a_pair_out_of_attempts_says_so_and_spends_no_rpc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One line and nothing else — the trace this pair never had while its channel lived on.

    The report used to leave the discussion group too, and every production row logged
    ``leave: "failed"``: the state that brings a pair here is Telegram saying the account is
    not in the chat, so the leave was two RPCs knocking on a group that had already ejected
    it — and in the one case it could have worked (a stale cached entity) it would have
    walked a healthy account out of a live chat.
    """
    campaign_id = await _campaign("acc-1", "acc-2")
    await _spend_the_budget("acc-1")
    # acc-2 comments here fine, so the channel is not going anywhere.
    await upsert_readiness("acc-2", _CHANNEL, joined=True, captcha_passed=True, ready=True)
    join = _patch_telegram(monkeypatch)

    await _rejoin.review_access_lost(datetime.now(UTC))

    assert join.calls == []
    lines = await _give_up_lines()
    assert "leave" not in lines[0]
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
    assert join.calls == []
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
    await mark_promoted_to_nc("acc-1")
    await mark_nc_handed_off("acc-1")
    await _spend_the_budget("acc-1")
    await upsert_readiness("acc-1", _OTHER, joined=True, captcha_passed=True, ready=True)
    _patch_telegram(monkeypatch)
    # The health gate reads warming signals this fixture does not build; selection's own
    # readiness filter — the thing under test — is the line below it.
    monkeypatch.setattr(_gates, "evaluate_readiness", lambda *_a, **_k: _Readiness(ready=True))

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
    assert join.calls == []


@pytest.mark.asyncio
async def test_a_dead_address_is_the_channel_rules_business_not_this_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A terminal verdict spends no attempt, so there is nothing for this rule to report."""
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
    assert join.calls == []


@pytest.mark.asyncio
async def test_only_a_deliberate_act_gets_a_finished_pair_back_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The verdict is final: time hands it nothing, a re-link hands it a whole new timeline.

    The stamp-freshness rule used to unmake it. Two windows after the last attempt the spent
    budget stopped counting, the review bought the pair a fresh attempt and poked onboarding
    — for a pair it had already reported as finished, forever, with ``rejoin_attempts``
    climbing past a budget the clamped line kept printing as "2/2". So the first half here is
    that NOTHING happens: no new stamp, no join RPC, no second line.

    The second half is the way back, and it is the one the log line tells the operator to
    use. Re-linking the channel clears the mark with the counter, and only then does the
    pass that re-joins reach the pair at all — which is what ``clear_rejoin_attempts`` is
    for, and why this goes through the rule and onboarding rather than the repository calls
    underneath them.
    """
    await _campaign("acc-1", "acc-2")
    await _spend_the_budget("acc-1")
    await upsert_readiness("acc-2", _CHANNEL, joined=True, captcha_passed=True, ready=True)
    join = _patch_telegram(monkeypatch)
    campaign_id = await fetch_active_campaign_for_channel(_CHANNEL)
    assert campaign_id is not None
    await _rejoin.review_access_lost(datetime.now(UTC))
    _backdate("acc-1", _CHANNEL, hours=49)  # two windows: what used to revive the budget

    await _rejoin.review_access_lost(datetime.now(UTC))
    await onboarding.onboard_account_channel("acc-1", _CHANNEL)

    stuck = await fetch_readiness("acc-1", _CHANNEL)
    assert stuck is not None
    assert (stuck.ready, stuck.rejoin_gave_up, stuck.rejoin_attempts) == (False, True, 2)
    assert join.calls == []
    assert len(await _give_up_lines()) == 1

    await deactivate_channel(campaign_id.campaign_id, _CHANNEL)
    await link_channel_to_campaign(campaign_id.campaign_id, _CHANNEL)
    await onboarding.onboard_account_channel("acc-1", _CHANNEL)

    row = await fetch_readiness("acc-1", _CHANNEL)
    assert row is not None
    assert (row.ready, row.rejoin_gave_up, row.rejoin_attempts) == (True, False, 0)


@pytest.mark.asyncio
async def test_every_reader_of_the_budget_stops_claiming_the_pair_is_worked_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three consumers read this rule's give-up test, and all three used to be lied to.

    They read it through ``exhausted`` and ``still_retrying``, so the mark reaching those
    two predicates is what makes the whole app agree. Asserted at the age that used to
    revive the budget — the board went back to «Возвращаемся в чат», the captcha queue
    listed the pair as being worked on, and ``_channel_pause`` read it as mid-timeline and
    held its channel drop open forever.
    """
    await _campaign("acc-1", "acc-2")
    await _spend_the_budget("acc-1")
    await upsert_readiness("acc-2", _CHANNEL, joined=True, captcha_passed=True, ready=True)
    _patch_telegram(monkeypatch)
    await _rejoin.review_access_lost(datetime.now(UTC))
    _backdate("acc-1", _CHANNEL, hours=49)

    now = datetime.now(UTC)
    row = await fetch_readiness("acc-1", _CHANNEL)
    assert row is not None
    assert board._channel_status([row], None, challenged=False, paused=False) == "join_failed"
    assert [p.account_id for p in await campaigns._rejoin_exhausted_pairs()] == ["acc-1"]
    assert _rejoin.still_retrying(row, now) is False


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
async def test_a_pair_that_got_back_in_mid_tick_is_not_marked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The review decides off a snapshot; onboarding may re-join a pair while it runs.

    An unconditional mark would badge a working pair — and since the mark became the
    verdict, it would also be the thing that never lets go of it again.
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
    assert join.calls == []
    row = await fetch_readiness("acc-1", _CHANNEL)
    assert row is not None
    assert row.rejoin_gave_up is False
