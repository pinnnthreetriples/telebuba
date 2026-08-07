"""The captcha rule's terminal half: the pair walks out, and the dead channel goes with it.

The retry is authorised in ``test_captcha_retry.py``; this file covers what happens when
it fails — ``captcha_gave_up`` persisted first, the group left best-effort after it, and
the channel unlinked only once every serving account has gone the same way. Plus the two
places the verdict has to be honoured elsewhere: onboarding must refuse a given-up pair
(without that, the next pass re-joins the group we just left), and ``bans``' own drop rule
must count it as terminal.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from telethon.tl.functions.channels import GetFullChannelRequest, LeaveChannelRequest

from core.config import settings
from core.db import (  # type: ignore[attr-defined]
    assign_account_to_campaign,
    create_account,
    create_campaign,
    fetch_readiness,
    insert_challenge,
    link_channel_to_campaign,
    list_campaign_channels,
    list_recent_logs,
    mark_pair_banned,
    stamp_captcha_retry,
    upsert_readiness,
)
from schemas.accounts import AccountCreate
from schemas.challenge import ChallengeInsert
from schemas.neurocomment import CampaignCreate
from schemas.telegram_actions import LeaveDiscussionGroup, WaitForBotChallenge
from services.neurocomment import _captcha_retry, _runtime, _seams, bans, onboarding
from tests.core.telegram_client.helpers import patch_action_client
from tests.services.neurocomment.onboarding_support import (
    _JoinStub,
    _no_sleep,
    _ReadStub,
    real_execute,
)

if TYPE_CHECKING:
    from schemas.logs import LogEntry
    from schemas.telegram_actions import TelegramAction

pytestmark = pytest.mark.usefixtures("isolate_onboarding")

_CHANNEL = "@chan"


async def _campaign(*accounts: str) -> str:
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    await link_channel_to_campaign(campaign.campaign_id, _CHANNEL)
    for account_id in accounts:
        await create_account(AccountCreate(account_id=account_id, session_name=account_id))
        await assign_account_to_campaign(campaign.campaign_id, account_id)
    return campaign.campaign_id


async def _block(account_id: str) -> None:
    """Park the pair as a lost bot challenge does: in the group, unable to speak."""
    await upsert_readiness(account_id, _CHANNEL, joined=True, captcha_passed=False, ready=False)
    await insert_challenge(
        ChallengeInsert(
            challenge_hash=f"h-{account_id}",
            account_id=account_id,
            channel=_CHANNEL,
            raw_text="press the duck",
            outcome="give_up",
        ),
    )


async def _working(account_id: str) -> None:
    """A sibling that passed the captcha and comments fine — the channel must survive it.

    Used wherever a test spans more than one tick. A merely BLOCKED sibling cannot play that
    part: the first tick authorises its own retry, so a later tick finds that retry spent
    too and retires it as well, which is correct behaviour but not what those tests are
    pinning.
    """
    await upsert_readiness(account_id, _CHANNEL, joined=True, captcha_passed=True, ready=True)


async def _retry_in_flight(account_id: str) -> None:
    """Authorised, not answered: the stamp is NEWER than the last readiness write."""
    await _block(account_id)
    await stamp_captcha_retry(account_id, _CHANNEL)


async def _retry_answered(account_id: str) -> None:
    """Authorised and answered: the re-solve came back and the pair is still blocked.

    The second ``upsert_readiness`` is what a failed re-solve writes, and its ``checked_at``
    landing after the stamp is the whole signal — without it the rule cannot tell a pass
    that has reported back from one still running.
    """
    await _retry_in_flight(account_id)
    await upsert_readiness(account_id, _CHANNEL, joined=True, captcha_passed=False, ready=False)


def _patch_telegram(monkeypatch: pytest.MonkeyPatch) -> _JoinStub:
    read = _ReadStub(linked_chat_id=4423, comments_enabled=True)
    actions = _JoinStub()
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", actions.execute)
    monkeypatch.setattr(onboarding.asyncio, "sleep", _no_sleep([]))
    monkeypatch.setattr(_runtime, "_ensure_onboarding_running", lambda *_a, **_k: None)
    return actions


def _leaves(actions: _JoinStub) -> list[TelegramAction]:
    return [
        action for _account, action in actions.calls if isinstance(action, LeaveDiscussionGroup)
    ]


def _leave_accounts(actions: _JoinStub) -> list[str]:
    return [
        account for account, action in actions.calls if isinstance(action, LeaveDiscussionGroup)
    ]


async def _events(event: str) -> list[LogEntry]:
    return [entry for entry in await list_recent_logs(limit=100) if entry.event == event]


async def _gave_up(account_id: str) -> bool:
    row = await fetch_readiness(account_id, _CHANNEL)
    assert row is not None
    return row.captcha_gave_up


async def _channel_is_active(campaign_id: str) -> bool:
    links = (await list_campaign_channels(campaign_id)).links
    return any(link.channel == _CHANNEL and link.active for link in links)


# --------------------------------------------------------------------------- #
# Spending the last attempt: the pair leaves the chat for good.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_an_answered_retry_that_is_still_blocked_ends_the_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole verdict in one tick: marked, out of the chat, and a line that says so."""
    await _campaign("acc-1", "acc-2")  # a second account, so the channel is not dropped too
    await _retry_answered("acc-1")
    await _block("acc-2")
    actions = _patch_telegram(monkeypatch)

    await _captcha_retry.review_captcha_blocked(datetime.now(UTC))

    assert await _gave_up("acc-1") is True
    assert len(_leaves(actions)) == 1
    [entry] = await _events("neurocomment_captcha_gave_up")
    assert entry.extra.get("leave") == "ok"
    assert entry.extra.get("reason") == "2/2"
    assert entry.account_id == "acc-1"


@pytest.mark.asyncio
async def test_a_retry_still_in_flight_is_left_alone_this_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stamp NEWER than the last readiness write means no pass has reported back yet.

    Onboarding takes minutes — resolve, jittered join, the solver's own timeouts — and the
    sweep ticks every five, so giving up the moment the stamp exists would kill a re-solve
    that is still running and take the account out of a chat it might have been let into.
    """
    await _campaign("acc-1")
    await _retry_in_flight("acc-1")
    actions = _patch_telegram(monkeypatch)

    await _captcha_retry.review_captcha_blocked(datetime.now(UTC))

    assert await _gave_up("acc-1") is False
    assert _leaves(actions) == []
    assert await _events("neurocomment_captcha_gave_up") == []


@pytest.mark.asyncio
async def test_a_retry_nobody_ever_answered_still_terminates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The unanswered-poke floor: a pair the pass can never reach must end anyway.

    The poke is best-effort — the account may sit at its rolling-24h join cap, the resolve
    may keep failing, the campaign may be stopped — so without this the pair would hold its
    channel open forever on a retry nothing was ever going to run.
    """
    now = datetime.now(UTC)
    await _campaign("acc-1", "acc-2")
    await _retry_in_flight("acc-1")
    await _working("acc-2")
    actions = _patch_telegram(monkeypatch)

    # Inside the window nothing happens; one ``channel_pause_hours`` later it does.
    await _captcha_retry.review_captcha_blocked(now + timedelta(hours=1))
    assert await _gave_up("acc-1") is False

    await _captcha_retry.review_captcha_blocked(
        now + timedelta(hours=settings.neurocomment.channel_pause_hours + 1),
    )
    assert await _gave_up("acc-1") is True
    assert len(_leaves(actions)) == 1


@pytest.mark.asyncio
async def test_a_failing_leave_rpc_does_not_undo_the_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mark is written BEFORE the leave, so a dead RPC cannot revive the pair.

    The other order would leave a pair the rule has finished with looking fresh, and the
    next onboarding pass would re-solve it — the exact loop this rule exists to end. The
    error class rides the log instead, which is all the operator can act on.
    """
    await _campaign("acc-1", "acc-2")
    await _retry_answered("acc-1")
    await _block("acc-2")
    _patch_telegram(monkeypatch)

    async def boom(_account_id: str, _action: TelegramAction) -> None:
        msg = "leave boom"
        raise RuntimeError(msg)

    monkeypatch.setattr(_seams, "execute", boom)

    await _captcha_retry.review_captcha_blocked(datetime.now(UTC))

    assert await _gave_up("acc-1") is True
    [entry] = await _events("neurocomment_captcha_gave_up")
    assert entry.extra.get("leave") == "RuntimeError"


@pytest.mark.asyncio
async def test_a_later_tick_does_not_leave_the_chat_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``captcha_gave_up`` is excluded from the bulk read, so the pair is never seen again.

    The sweep ticks every five minutes forever; a rule that re-issued its verdict would send
    a leave RPC per tick for a chat we are already out of.
    """
    await _campaign("acc-1", "acc-2")
    await _retry_answered("acc-1")
    await _working("acc-2")
    actions = _patch_telegram(monkeypatch)
    now = datetime.now(UTC)

    await _captcha_retry.review_captcha_blocked(now)
    await _captcha_retry.review_captcha_blocked(now + timedelta(days=7))

    assert len(_leaves(actions)) == 1
    assert len(await _events("neurocomment_captcha_gave_up")) == 1


@pytest.mark.asyncio
async def test_onboarding_refuses_a_pair_that_gave_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without this guard the next pass re-joins the group we just left and re-solves.

    That is the loop the whole rule exists to end, so the verdict has to be terminal at the
    onboarding gate too — no join RPC, no solver call, and ``bot_challenge`` as the state,
    because the guardian bot really is still the wall.
    """
    await _campaign("acc-1")
    await _retry_answered("acc-1")
    await _captcha_retry.review_captcha_blocked(datetime.now(UTC))
    actions = _patch_telegram(monkeypatch)
    read = _ReadStub(linked_chat_id=4423, comments_enabled=True)
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)

    outcome = await onboarding.onboard_account_channel("acc-1", _CHANNEL)

    assert outcome.state == "bot_challenge"
    assert actions.calls == []  # no join RPC
    assert not any(isinstance(action, WaitForBotChallenge) for _account, action in read.calls)


# --------------------------------------------------------------------------- #
# The channel drop, and the coverage rule that keeps it honest.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_channel_every_serving_account_gave_up_on_is_unlinked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nobody got past the bot: every account walks out, and then the channel goes.

    A fleet of six, because the two things here are easy to confuse at a size of two. The
    unlink is campaign bookkeeping — it takes NOBODY out of the discussion chat. Membership
    is per account, so each of the six has to leave on its own, and a rule that dropped the
    channel after one leave would leave five accounts sitting in a chat they can never
    write in, invisible to every rule from then on because the channel is no longer linked.
    """
    fleet = [f"acc-{n}" for n in range(1, 7)]
    campaign_id = await _campaign(*fleet)
    for account_id in fleet:
        await _retry_answered(account_id)
    actions = _patch_telegram(monkeypatch)

    await _captcha_retry.review_captcha_blocked(datetime.now(UTC))

    # One leave per account, nobody twice, nobody missed.
    assert sorted(_leave_accounts(actions)) == fleet
    for account_id in fleet:
        assert await _gave_up(account_id) is True
    assert await _channel_is_active(campaign_id) is False
    [entry] = await _events("neurocomment_channel_captcha_unsolved")
    assert entry.extra.get("gave_up_accounts") == len(fleet)
    assert entry.extra.get("reason") == "captcha_unsolved"
    assert entry.extra.get("campaign_id") == campaign_id


@pytest.mark.asyncio
async def test_a_serving_account_never_tried_here_keeps_the_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing readiness row means "never onboarded here", not "failed here".

    Onboarding reaches a fleet slowly (no timer, jittered joins, a join cap), so counting
    only the rows that exist would let the first pair to give up drop a channel the other
    accounts have not even attempted.
    """
    campaign_id = await _campaign("acc-1", "acc-2")
    await _retry_answered("acc-1")  # acc-2 has no readiness row at all
    _patch_telegram(monkeypatch)

    await _captcha_retry.review_captcha_blocked(datetime.now(UTC))

    assert await _gave_up("acc-1") is True
    assert await _channel_is_active(campaign_id) is True
    assert await _events("neurocomment_channel_captcha_unsolved") == []


@pytest.mark.asyncio
async def test_one_working_account_keeps_the_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    """One stubborn pair must never kill a channel the other accounts comment in fine."""
    campaign_id = await _campaign("acc-1", "acc-2")
    await _retry_answered("acc-1")
    await upsert_readiness("acc-2", _CHANNEL, joined=True, captcha_passed=True, ready=True)
    _patch_telegram(monkeypatch)

    await _captcha_retry.review_captcha_blocked(datetime.now(UTC))

    assert await _gave_up("acc-1") is True
    assert await _channel_is_active(campaign_id) is True


@pytest.mark.asyncio
async def test_the_ban_drop_rule_counts_a_give_up_as_terminal() -> None:
    """``bans``' own drop rule runs on the post hot path with no clock — and needs all three.

    It is kept alongside this rule's drop deliberately: a ban lands mid-post, where waiting
    out somebody else's 48h is not an option. But without the third terminal state, five
    pairs that gave up on the captcha plus one ban would hold a dead channel forever, since
    neither verdict has a path back.
    """
    campaign_id = await _campaign("acc-1", "acc-2")
    await _block("acc-1")
    await mark_pair_banned("acc-1", _CHANNEL)
    await _retry_answered("acc-2")
    await _captcha_retry._give_up_and_leave("acc-2", _CHANNEL)

    await bans._unlink_channel_if_no_account_left("acc-1", _CHANNEL)

    assert await _channel_is_active(campaign_id) is False


@pytest.mark.asyncio
async def test_the_leave_reaches_telegram_as_a_real_leave_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End to end with only Telethon stubbed: the account really walks out of the chat.

    Every other test in this file takes ``isolate_onboarding``'s ``_seams.execute`` stub,
    which proves the rule ASKS for a leave and nothing whatever about one happening. Here
    that default is put back to the real gateway and the Telethon client is stubbed
    underneath it instead, so the entire chain has to hold:
    the rule's ``LeaveDiscussionGroup`` → ``core.telegram_client.execute``'s dispatch arm
    → ``GetFullChannelRequest`` to resolve the linked discussion group →
    ``LeaveChannelRequest`` against THAT group rather than the broadcast channel the
    operator typed. A seam patched over the top of any one of those links would hide a
    rule that logs "the account left" while the account is still sitting in the chat.
    """
    await _campaign("acc-1", "acc-2")
    await _retry_answered("acc-1")
    await _working("acc-2")
    monkeypatch.setattr(_runtime, "_ensure_onboarding_running", lambda *_a, **_k: None)
    monkeypatch.setattr(_seams, "execute", real_execute)

    captured: list[object] = []
    linked_entity = MagicMock(id=4423)

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> object:
            captured.append(request)
            if isinstance(request, GetFullChannelRequest):
                full = MagicMock()
                full.full_chat = MagicMock(linked_chat_id=4423)
                full.chats = [MagicMock(id=999), linked_entity]
                return full
            return None

    patch_action_client(monkeypatch, FakeClient())

    await _captcha_retry.review_captcha_blocked(datetime.now(UTC))

    assert await _gave_up("acc-1") is True
    leaves = [request for request in captured if isinstance(request, LeaveChannelRequest)]
    assert len(leaves) == 1
    # The linked discussion group, not the broadcast channel: commenting membership lives
    # in the group, and leaving the wrong peer would leave the account able to comment.
    assert leaves[0].channel is linked_entity
    [entry] = await _events("neurocomment_captcha_gave_up")
    assert entry.extra.get("leave") == "ok"
