"""``already_participant`` is not a re-join, so it does not hand the budget back (#43).

``_classify._solve_and_record`` clears the re-join counter because being in the group means
whatever kicked us out is over. Telegram answering ``already_participant`` says something
else: it never let us out, so the parking that spent the attempt was wrong — a stale group
entity in the session cache, or a channel-level refusal read as an account-level one.
Resetting on that closed a loop with no bound: the sweep parks the pair, the review spends
an attempt and pokes onboarding, the join answers ``already_participant``, the counter goes
back to zero, and five minutes later the same tick does it again. Up to 288 join RPCs a day
for one pair, and invisible to the rolling-24h join cap, because ``record_join`` only counts
a join that actually happened.

Own module because ``test_rejoin`` is at the 700-line test cap.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import (  # type: ignore[attr-defined]
    _get_engine,
    assign_account_to_campaign,
    claim_comment,
    create_account,
    create_campaign,
    fetch_comment,
    fetch_readiness,
    link_channel_to_campaign,
    list_campaign_channels,
    mark_comment_posted,
    stamp_rejoin_attempt,
    upsert_readiness,
)
from core.telegram_client import TelegramReadError
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate
from schemas.telegram_actions import (
    BotChallengeWaitResult,
    CheckMessagesAlive,
    CheckMessagesAliveResult,
    LinkedDiscussionGroupResult,
    WaitForBotChallenge,
)
from services import neurocomment
from services.neurocomment import _rejoin, _runtime, _seams, _sweep_read, onboarding
from tests.services.neurocomment.onboarding_support import _JoinStub, _no_sleep, _ReadStub

if TYPE_CHECKING:
    from schemas.neurocomment import CommentRecord
    from schemas.telegram_actions import ActionStatus, TelegramAction, TelegramReadAction

pytestmark = pytest.mark.usefixtures("isolate_onboarding")

_CHANNEL = "@chan"


async def _campaign(*accounts: str) -> str:
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    await link_channel_to_campaign(campaign.campaign_id, _CHANNEL)
    for account_id in accounts:
        await create_account(AccountCreate(account_id=account_id, session_name=account_id))
        await assign_account_to_campaign(campaign.campaign_id, account_id)
    return campaign.campaign_id


async def _park(account_id: str, *, attempts: int = 0) -> None:
    """Leave the pair exactly as a post-time access loss does, with ``attempts`` spent.

    A *spent* attempt is one an onboarding pass already answered, so each stamp is followed
    by the re-park a failed re-join writes — that later readiness write is what tells
    onboarding the attempt is no longer owed to it (``_rejoin.attempt_owed``).
    """
    await upsert_readiness(account_id, _CHANNEL, joined=False, captcha_passed=True, ready=False)
    for _ in range(attempts):
        await stamp_rejoin_attempt(account_id, _CHANNEL)
        await upsert_readiness(account_id, _CHANNEL, joined=False, captcha_passed=True, ready=False)


async def _rejoin_budget(account_id: str) -> tuple[int, bool]:
    """``(attempts spent, is there still a stamp)`` — the two columns the reset touches."""
    row = await fetch_readiness(account_id, _CHANNEL)
    assert row is not None
    return row.rejoin_attempts, row.rejoin_attempted_at is not None


def _pokes(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Capture the onboarding pokes instead of spawning a real pass."""
    triggered: list[object] = []
    monkeypatch.setattr(_runtime, "_ensure_onboarding_running", triggered.append)
    return triggered


def _patch_joins(monkeypatch: pytest.MonkeyPatch, *, status: ActionStatus) -> _JoinStub:
    """Onboarding's two seams, with the join answering ``status`` for ``_CHANNEL``."""
    join = _JoinStub()
    join.set(_CHANNEL, status=status)
    read = _ReadStub(linked_chat_id=4423, comments_enabled=True)
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)
    monkeypatch.setattr(onboarding.asyncio, "sleep", _no_sleep([]))
    return join


@pytest.mark.asyncio
async def test_an_already_participant_answer_does_not_hand_the_rejoin_budget_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pair is comment-able again, and the attempt it spent stays spent.

    Both halves matter: refusing to reset must not cost the pair its readiness (it IS in the
    group), and keeping the count is what lets the budget bound a loop nothing else bounds.
    """
    budget = settings.neurocomment.channel_max_rounds
    campaign_id = await _campaign("acc-1")
    await _park("acc-1", attempts=budget - 1)
    _pokes(monkeypatch)
    join = _patch_joins(monkeypatch, status="already_participant")

    await _rejoin.review_access_lost(datetime.now(UTC) + timedelta(hours=25))
    result = await neurocomment.onboard_campaign(campaign_id)

    assert [account_id for account_id, _ in join.calls] == ["acc-1"]
    assert [outcome.state for outcome in result.outcomes] == ["ready"]
    # The attempt the review just spent is still on the row, stamp included: the next tick
    # reads a pair one attempt from its budget rather than one that never tried.
    assert await _rejoin_budget("acc-1") == (budget, True)


@pytest.mark.asyncio
async def test_a_join_that_actually_happened_still_hands_the_rejoin_budget_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The behaviour the ``already_participant`` case was carved out of, pinned here too.

    ``ok`` means Telegram let the account back in, so whatever kicked it out is over and the
    NEXT access loss starts from attempt one. Only this answer resets.
    """
    budget = settings.neurocomment.channel_max_rounds
    campaign_id = await _campaign("acc-1")
    await _park("acc-1", attempts=budget - 1)
    _pokes(monkeypatch)
    join = _patch_joins(monkeypatch, status="ok")

    await _rejoin.review_access_lost(datetime.now(UTC) + timedelta(hours=25))
    result = await neurocomment.onboard_campaign(campaign_id)

    assert [account_id for account_id, _ in join.calls] == ["acc-1"]
    assert [outcome.state for outcome in result.outcomes] == ["ready"]
    assert await _rejoin_budget("acc-1") == (0, False)


# --------------------------------------------------------------------------- #
# The whole loop: sweep parks, review spends, onboarding joins, and round again.
# --------------------------------------------------------------------------- #


def _kicked() -> TelegramReadError:
    """The shape ``execute_read`` gives a Telethon RPC failure (``_read.execute_read_many``).

    The class name behind the ``RPC: `` prefix is the only machine-readable half of a kicked
    read, and the only thing ``_sweep_read._lost_access_error`` will park a pair on.
    """
    return TelegramReadError("RPC: ChannelPrivateError")


class _Reads:
    """One ``execute_read`` for both halves of the loop, since they share the seam.

    The sweep's re-read (``CheckMessagesAlive``) answers a kick for ``kicked`` and an
    ordinary result for everyone else; the join's linked-group resolve and the solver's
    challenge wait answer as they do for any healthy channel.
    """

    def __init__(self, kicked: str) -> None:
        self.kicked = kicked

    async def execute_read(self, account_id: str, action: TelegramReadAction) -> object:
        if isinstance(action, CheckMessagesAlive):
            if account_id == self.kicked:
                raise _kicked()
            return CheckMessagesAliveResult(missing_ids=[])
        if isinstance(action, WaitForBotChallenge):
            return BotChallengeWaitResult(message=None)
        return LinkedDiscussionGroupResult(linked_chat_id=4423, comments_enabled=True)


async def _two_authors(campaign_id: str) -> list[CommentRecord]:
    """A delivered comment for each account, ``acc-2``'s the freshest one.

    ``_sweep_read`` walks the authors freshest-comment first, so the order decides which
    reader's verdict the walk actually sees. Written onto the row rather than left to two
    consecutive claims, so the walk is the same on every run.
    """
    for post_id, account_id in enumerate(("acc-1", "acc-2"), start=1):
        await claim_comment(_CHANNEL, post_id, campaign_id, account_id)
        await mark_comment_posted(
            _CHANNEL, post_id, comment_text=f"text {post_id}", comment_msg_id=100 + post_id
        )
    stale = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    with _get_engine().begin() as connection:
        connection.exec_driver_sql(
            "UPDATE neurocomment_comments SET created_at = ? WHERE channel = ? AND post_id = 1",
            (stale, _CHANNEL),
        )
    comments = [await fetch_comment(_CHANNEL, post_id) for post_id in (1, 2)]
    assert all(comment is not None for comment in comments)
    return [comment for comment in comments if comment is not None]


def _joins(calls: list[tuple[str, TelegramAction]]) -> list[str]:
    """The account behind every ``JoinDiscussionGroup`` RPC, in order.

    Only that action: the same seam carries the leave and the comment, and the claim here is
    about how many times the fleet asked Telegram to join this group.
    """
    return [
        account_id for account_id, action in calls if action.action_type == "join_discussion_group"
    ]


@pytest.mark.asyncio
async def test_a_pair_telegram_keeps_calling_a_participant_costs_the_budget_not_a_join_a_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The loop end to end, and the only thing that bounds it is the re-join budget.

    Each round is one sweep tick's worth of the real thing: ``_sweep_read`` parks the reader
    Telegram will not answer for, ``_rejoin.review_access_lost`` spends an attempt and pokes
    onboarding, and the poked pass joins — to be told the account was a participant all
    along, which writes the pair ready again and leaves the sweep free to park it on the next
    tick. With the reset firing on that answer the counter never survived a round, so the
    third round joined exactly like the first: the bound was the tick rate, 288 joins a day
    for one pair, none of them visible to the join cap. The count is what ends it.

    ``acc-1`` keeps a working row throughout: it is the author whose read succeeds (so the
    kick is read as ``acc-2``'s and not the channel's) and the ready pair that stops
    ``_rejoin`` unlinking a channel the campaign is still commenting in.
    """
    # Pinned, unlike the two tests above: this one counts ROUNDS against the budget, so the
    # operator retuning either setting must not silently change what it is asserting.
    monkeypatch.setattr(settings.neurocomment, "channel_max_rounds", 2)
    monkeypatch.setattr(settings.neurocomment, "channel_pause_hours", 24.0)
    campaign_id = await _campaign("acc-1", "acc-2")
    for account_id in ("acc-1", "acc-2"):
        await upsert_readiness(account_id, _CHANNEL, joined=True, captcha_passed=True, ready=True)
    comments = await _two_authors(campaign_id)
    _pokes(monkeypatch)
    join = _JoinStub()
    join.set(_CHANNEL, status="already_participant")
    monkeypatch.setattr(_seams, "execute_read", _Reads("acc-2").execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)
    monkeypatch.setattr(onboarding.asyncio, "sleep", _no_sleep([]))

    # Three rounds against a budget of two — a day apart, which is the tempo the rule is
    # written for; the loop it could not bound ran this same round every five minutes.
    for round_number in range(3):
        await _sweep_read.read_alive(_CHANNEL, comments, [101, 102])
        await _rejoin.review_access_lost(datetime.now(UTC) + timedelta(hours=25))
        await neurocomment.onboard_campaign(campaign_id)
        assert await _rejoin_budget("acc-2") == (min(round_number + 1, 2), True)

    assert _joins(join.calls) == ["acc-2", "acc-2"]  # the budget, not one per round
    # And the channel is still the campaign's: a pair Telegram keeps calling a participant is
    # not evidence against the chat, and ``acc-1`` is commenting in it.
    links = (await list_campaign_channels(campaign_id)).links
    assert [link.active for link in links] == [True]
