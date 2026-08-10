"""The deletion sweep reads as EVERY comment author in turn, not as ``comments[0]``.

The sweep can only re-read a channel's comments as somebody who is in its discussion
group, and its members are the accounts that commented there. Reading as whoever the
repository returned first meant one kicked account silenced the check for good: a live day
produced 136 ``RPC: ChannelPrivateError`` lines on one channel and 37 on another, all from
the same account, and the deletions those channels made were never noticed. Own module
because ``test_runtime_sweep`` sits exactly on the 700-line cap.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.db import (  # type: ignore[attr-defined]
    _get_engine,
    claim_comment,
    create_account,
    create_campaign,
    fetch_comment,
    fetch_readiness,
    link_channel_to_campaign,
    list_recent_logs,
    mark_captcha_gave_up,
    mark_comment_posted,
    mark_human_skipped,
    mark_pair_banned,
    upsert_readiness,
)
from core.telegram_client import TelegramAccountNotFoundError, TelegramReadError
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate
from schemas.telegram_actions import BanCheckResult, CheckMessagesAlive, CheckMessagesAliveResult
from services.neurocomment import _rejoin, _sweep, _sweep_read

if TYPE_CHECKING:
    from collections.abc import Iterator

    from schemas.logs import LogEntry

pytestmark = pytest.mark.usefixtures("isolate_runtime")

_CHANNEL = "@a"


@pytest.fixture(autouse=True)
def _forget_read_mutes() -> Iterator[None]:
    """The all-kicked mute is process state, so it must not travel between tests."""
    _sweep_read.reset_read_mutes()
    yield
    _sweep_read.reset_read_mutes()


def _kicked(error_type: str = "ChannelPrivateError") -> TelegramReadError:
    """The shape ``execute_read`` gives a Telethon RPC failure (``_read.execute_read_many``)."""
    return TelegramReadError(f"RPC: {error_type}")


class _Reader:
    """Scripted ``_seams.execute_read``: an outcome per account, recording the call order."""

    def __init__(self, outcomes: dict[str, object]) -> None:
        self.outcomes = outcomes
        self.calls: list[str] = []

    async def __call__(self, account_id: str, action: CheckMessagesAlive) -> object:
        self.calls.append(account_id)
        outcome = self.outcomes.get(account_id, CheckMessagesAliveResult(missing_ids=[]))
        if isinstance(outcome, Exception):
            raise outcome
        assert action.channel == _CHANNEL
        return outcome


async def _campaign_with_authors(*authors: str) -> None:
    """One delivered comment per author on ``@a``; post/msg ids follow the author order.

    Author ``n`` owns post ``n`` and message id ``100 + n``, and its comment is stamped one
    minute newer than the previous one — so the LAST author holds the freshest comment as a
    fact of the data, not of the row order the repository happens to return.
    """
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    await link_channel_to_campaign(campaign.campaign_id, _CHANNEL)
    now = datetime.now(UTC)
    for post_id, account_id in enumerate(authors, start=1):
        await create_account(AccountCreate(account_id=account_id, session_name=account_id))
        await claim_comment(_CHANNEL, post_id, campaign.campaign_id, account_id)
        await mark_comment_posted(_CHANNEL, post_id, comment_text="x", comment_msg_id=100 + post_id)
        created = (now - timedelta(minutes=len(authors) - post_id)).isoformat()
        with _get_engine().begin() as connection:
            connection.exec_driver_sql(
                "UPDATE neurocomment_comments SET created_at = ? WHERE channel = ? AND post_id = ?",
                (created, _CHANNEL, post_id),
            )


def _patch_reader(monkeypatch: pytest.MonkeyPatch, reader: _Reader) -> _Reader:
    monkeypatch.setattr("services.neurocomment._seams.execute_read", reader)
    return reader


async def _ban_pair(account_id: str) -> None:
    """The row ``bans._mark_banned_and_leave`` leaves behind, written the same way."""
    await upsert_readiness(account_id, _CHANNEL, joined=True, captcha_passed=False, ready=False)
    await mark_pair_banned(account_id, _CHANNEL)


async def _skip_pair(account_id: str) -> None:
    """The row an operator's «Пропустить» (#148) leaves behind."""
    await upsert_readiness(account_id, _CHANNEL, joined=True, captcha_passed=True, ready=True)
    await mark_human_skipped(account_id, _CHANNEL)


async def _captcha_retire_pair(account_id: str) -> None:
    """The row ``_captcha_retry._give_up_and_leave`` leaves: marked, then walked out of chat."""
    await upsert_readiness(account_id, _CHANNEL, joined=True, captcha_passed=False, ready=False)
    await mark_captcha_gave_up(account_id, _CHANNEL)


async def _read_failures() -> list[LogEntry]:
    return [
        entry
        for entry in await list_recent_logs(limit=50)
        if entry.event == "neurocomment_sweep_read_failed"
    ]


@pytest.mark.asyncio
async def test_a_kicked_reader_hands_the_check_to_the_next_author(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The freshest author is out → the next one answers, and the check still happens."""
    await _campaign_with_authors("acc-1", "acc-2")
    reader = _patch_reader(
        monkeypatch,
        _Reader(
            {
                "acc-2": _kicked(),  # freshest author, kicked out of the group
                "acc-1": CheckMessagesAliveResult(missing_ids=[101]),
            },
        ),
    )

    await _sweep._sweep_once()

    assert reader.calls == ["acc-2", "acc-1"]  # freshest first, then the fallback
    gone = await fetch_comment(_CHANNEL, 1)
    assert gone is not None
    assert gone.deleted_at is not None  # the deletion check ran on the second reader's answer
    assert await _read_failures() == []  # somebody read it, so nothing to report


@pytest.mark.asyncio
async def test_a_kicked_reader_is_parked_for_the_rejoin_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not a warning any more: the pair carries the sentinel ``_rejoin.access_lost`` reads."""
    await _campaign_with_authors("acc-1", "acc-2")
    _patch_reader(monkeypatch, _Reader({"acc-2": _kicked()}))

    await _sweep._sweep_once()

    parked = await fetch_readiness("acc-2", _CHANNEL)
    assert parked is not None
    assert (parked.joined, parked.captcha_passed, parked.ready) == (False, True, False)
    assert parked.access_lost_reason == "ChannelPrivateError"
    assert _rejoin.access_lost(parked) is True  # the re-join review will pick it up
    # Only the account that actually failed: the reader that worked is untouched.
    assert await fetch_readiness("acc-1", _CHANNEL) is None


@pytest.mark.asyncio
async def test_every_author_kicked_blames_the_channel_and_parks_nobody(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every reader out on the same tick is the CHANNEL, and parking them all would cost it.

    ``CheckMessagesAlive`` resolves the BROADCAST channel before it reaches the discussion
    group (``GetFullChannelRequest``), so a channel that went private answers
    ``ChannelPrivateError`` to every account in turn. Parking on that read a channel-wide
    fact as a per-account one: no pair left ready, the re-join review spending an attempt on
    each, and ``_rejoin._drop_channel_if_nothing_works`` unlinking the channel 48h later —
    where the same failure on ``main`` cost one log line. It costs one log line again.
    """
    await _campaign_with_authors("acc-1", "acc-2")
    _patch_reader(
        monkeypatch,
        # Both names in the family, because both reach here: the discussion-group RPCs
        # answer USER_NOT_PARTICIPANT once we are out, CHANNEL_PRIVATE once it is unreachable.
        _Reader({"acc-2": _kicked(), "acc-1": _kicked("UserNotParticipantError")}),
    )

    await _sweep._sweep_once()

    assert await fetch_readiness("acc-2", _CHANNEL) is None  # no pair pulled out of service
    assert await fetch_readiness("acc-1", _CHANNEL) is None
    failures = await _read_failures()
    assert len(failures) == 1  # and one line for the channel on this tick, as before
    # The signature the line has to carry: everybody tried said the same thing, and that is
    # why nothing was parked. A smaller ``readers_kicked`` would have been the accounts.
    assert failures[0].extra["readers_tried"] == 2
    assert failures[0].extra["readers_kicked"] == 2
    assert failures[0].extra["readers_parked"] == 0
    assert failures[0].extra["channel"] == _CHANNEL
    assert failures[0].extra["reason"] == "RPC: UserNotParticipantError"  # the last failure
    assert failures[0].account_id == "acc-1"


@pytest.mark.asyncio
async def test_one_kicked_author_out_of_three_is_parked_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rotation's whole point, and the all-kicked guard must not swallow it.

    The denominator is the readers actually TRIED, not the channel's authors: ``acc-1`` never
    gets a turn (``acc-2`` answered), so the walk ends 1-kicked-of-2-tried — accounts, not
    channel — and the one Telegram named is parked.
    """
    await _campaign_with_authors("acc-1", "acc-2", "acc-3")
    reader = _patch_reader(
        monkeypatch,
        _Reader({"acc-3": _kicked(), "acc-2": CheckMessagesAliveResult(missing_ids=[101])}),
    )

    await _sweep._sweep_once()

    assert reader.calls == ["acc-3", "acc-2"]  # freshest first, then the one that answered
    parked = await fetch_readiness("acc-3", _CHANNEL)
    assert parked is not None
    assert _rejoin.access_lost(parked) is True
    assert parked.access_lost_reason == "ChannelPrivateError"
    for account_id in ("acc-1", "acc-2"):  # the untried and the working one are untouched
        assert await fetch_readiness(account_id, _CHANNEL) is None
    gone = await fetch_comment(_CHANNEL, 1)
    assert gone is not None
    assert gone.deleted_at is not None  # and the deletion check still ran on the answer
    assert await _read_failures() == []


@pytest.mark.asyncio
async def test_a_lone_author_is_not_parked_on_its_own_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One account cannot prove which of the two things happened, so it proves neither.

    A lone author IS every author, so it takes the same branch and for the same reason: a
    false park costs a working pair and starts the 48h countdown to unlinking a channel that
    may be fine, while a missed one costs one read per tick — and the pair stays ``ready``,
    so the channel's next post is attempted with it and ``_outcomes`` parks it on these same
    verdicts with proof that it tried to WRITE.
    """
    await _campaign_with_authors("acc-1")
    _patch_reader(monkeypatch, _Reader({"acc-1": _kicked()}))

    await _sweep._sweep_once()

    assert await fetch_readiness("acc-1", _CHANNEL) is None  # still selectable for posting
    failures = await _read_failures()
    assert len(failures) == 1
    assert (failures[0].extra["readers_tried"], failures[0].extra["readers_kicked"]) == (1, 1)
    assert failures[0].extra["readers_parked"] == 0


@pytest.mark.asyncio
async def test_the_tick_stays_silent_once_every_author_is_parked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A parked pair is the re-join rule's; re-reading with it is the old five-minute drip.

    Seeded by hand rather than by a previous tick, because the sweep no longer parks a whole
    channel's authors itself (that is the channel talking) — the rows still arrive here from
    ``_outcomes`` and ``_classify``, and the sweep must stay off them.
    """
    await _campaign_with_authors("acc-1", "acc-2")
    for account_id in ("acc-1", "acc-2"):
        await upsert_readiness(
            account_id,
            _CHANNEL,
            joined=False,
            captcha_passed=True,
            ready=False,
            access_lost_reason="ChannelPrivateError",
        )
    reader = _patch_reader(monkeypatch, _Reader({"acc-1": _kicked(), "acc-2": _kicked()}))

    await _sweep._sweep_once()

    assert reader.calls == []  # the tick asked nobody
    assert await _read_failures() == []  # and reported nothing, tick after tick


@pytest.mark.asyncio
async def test_a_pair_the_rejoin_rule_owns_is_not_re_stamped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-writing a parked row would cancel a re-join the budget has already been charged.

    ``upsert_readiness`` moves ``checked_at``, and ``_rejoin.attempt_owed`` reads an attempt
    stamp NEWER than that write as "still owed". Re-parking a pair every five minutes would
    push ``checked_at`` past the stamp the review just spent, the poked onboarding pass
    would skip the pair as already answered, and the attempt would be gone for a day.
    """
    await _campaign_with_authors("acc-1", "acc-2")
    await upsert_readiness(
        "acc-2",
        _CHANNEL,
        joined=False,
        captcha_passed=True,
        ready=False,
        access_lost_reason="ChannelPrivateError",
    )
    before = await fetch_readiness("acc-2", _CHANNEL)
    reader = _patch_reader(monkeypatch, _Reader({}))

    await _sweep._sweep_once()

    assert reader.calls == ["acc-1"]  # the parked freshest author is skipped, not re-read
    after = await fetch_readiness("acc-2", _CHANNEL)
    assert before is not None
    assert after is not None
    assert after.checked_at == before.checked_at


@pytest.mark.asyncio
async def test_a_banned_author_is_never_read_with(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The group banned this account and it walked out — asking it again is the anti-ban risk.

    ``_rejoin.access_lost`` excludes banned rows by construction, so the parked-pair skip
    never covered this one: the sweep re-read as the banned account on every five-minute
    tick for as long as its comments stayed in the lookback window (288 ticks a day), and
    every failure re-wrote the row into the access-loss sentinel.
    """
    await _campaign_with_authors("acc-1")
    await _ban_pair("acc-1")
    before = await fetch_readiness("acc-1", _CHANNEL)
    reader = _patch_reader(monkeypatch, _Reader({"acc-1": _kicked()}))

    await _sweep._sweep_once()

    assert reader.calls == []  # Telegram is not touched at all
    assert await fetch_readiness("acc-1", _CHANNEL) == before  # the ban row is intact
    assert await _read_failures() == []  # nobody was tried, so there is nothing to report


@pytest.mark.asyncio
async def test_an_operator_skip_survives_the_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A skipped pair is out of service by hand; the sweep must not overwrite that row.

    Onboarding refuses to re-join a skipped pair, so parking one buys nothing and costs
    the operator's mark — ``upsert_readiness`` would leave it unjoined + captcha_passed.
    """
    await _campaign_with_authors("acc-1", "acc-2")
    await _skip_pair("acc-2")  # the freshest author
    before = await fetch_readiness("acc-2", _CHANNEL)
    reader = _patch_reader(
        monkeypatch,
        _Reader({"acc-2": _kicked(), "acc-1": CheckMessagesAliveResult(missing_ids=[101])}),
    )

    await _sweep._sweep_once()

    assert reader.calls == ["acc-1"]  # skipped pair passed over, the walk goes on
    after = await fetch_readiness("acc-2", _CHANNEL)
    assert after == before
    assert after is not None
    assert (after.human_skipped, after.joined) == (True, True)
    gone = await fetch_comment(_CHANNEL, 1)
    assert gone is not None
    assert gone.deleted_at is not None  # the check still ran on the fallback author


@pytest.mark.asyncio
async def test_a_missing_account_hands_the_check_to_the_next_author(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An account deleted from the fleet is our bookkeeping, not a verdict about the chat.

    ``execute_read_many`` raises ``TelegramAccountNotFoundError`` BEFORE its try block, so
    it is not a ``TelegramReadError``: it used to end the walk on the first candidate —
    the one-account-silences-the-channel failure, through another door.
    """
    await _campaign_with_authors("acc-1", "acc-2")
    reader = _patch_reader(
        monkeypatch,
        _Reader(
            {
                "acc-2": TelegramAccountNotFoundError("Account not found: acc-2"),
                "acc-1": CheckMessagesAliveResult(missing_ids=[101]),
            },
        ),
    )

    await _sweep._sweep_once()

    assert reader.calls == ["acc-2", "acc-1"]
    gone = await fetch_comment(_CHANNEL, 1)
    assert gone is not None
    assert gone.deleted_at is not None
    assert await fetch_readiness("acc-2", _CHANNEL) is None  # no false access loss
    assert await _read_failures() == []  # somebody read it


@pytest.mark.asyncio
async def test_an_off_contract_gateway_answer_still_leaves_a_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Returning ``None`` for a wrong result type stopped the check with nothing logged."""
    await _campaign_with_authors("acc-1", "acc-2")
    reader = _patch_reader(monkeypatch, _Reader({"acc-2": BanCheckResult(state="can_send")}))

    await _sweep._sweep_once()

    assert reader.calls == ["acc-2"]  # the next account would not answer differently
    failures = await _read_failures()
    assert len(failures) == 1
    assert failures[0].extra["error_type"] == "BanCheckResult"
    assert failures[0].extra["reason"] == "unexpected_result"
    assert (failures[0].extra["readers_tried"], failures[0].extra["readers_parked"]) == (1, 0)
    assert await fetch_readiness("acc-2", _CHANNEL) is None  # not a membership verdict


@pytest.mark.asyncio
async def test_a_bare_gateway_code_is_not_read_as_a_kick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only a ``RPC: `` reason parks a pair — a ``ChannelGatewayError`` code is not one.

    ``execute_read_many`` wraps that family as ``TelegramReadError(exc.code)`` with no
    prefix, so matching on the reason with the prefix merely stripped compared against a
    bare gateway code. No code spells one of these names today; this keeps it that way.
    """
    await _campaign_with_authors("acc-1", "acc-2")
    gateway_code = TelegramReadError("ChannelPrivateError")  # no ``RPC: `` prefix
    reader = _patch_reader(monkeypatch, _Reader({"acc-2": gateway_code}))

    await _sweep._sweep_once()

    assert reader.calls == ["acc-2"]  # not a membership verdict → the walk ends, as for a fault
    assert await fetch_readiness("acc-2", _CHANNEL) is None  # and nobody is parked
    failures = await _read_failures()
    assert len(failures) == 1
    assert failures[0].extra["readers_parked"] == 0


@pytest.mark.asyncio
async def test_a_flood_wait_parks_nobody_and_ends_the_walk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rate limit says nothing about membership, and burning the rest would multiply it."""
    await _campaign_with_authors("acc-1", "acc-2")
    reader = _patch_reader(
        monkeypatch,
        _Reader({"acc-2": TelegramReadError("FloodWait(42s)", kind="flood_wait", seconds=42)}),
    )

    await _sweep._sweep_once()

    assert reader.calls == ["acc-2"]  # the walk stopped instead of spending acc-1 too
    assert await fetch_readiness("acc-2", _CHANNEL) is None  # no false access loss
    assert await fetch_readiness("acc-1", _CHANNEL) is None
    failures = await _read_failures()
    assert len(failures) == 1
    assert failures[0].extra["readers_tried"] == 1
    assert failures[0].extra["kind"] == "flood_wait"


@pytest.mark.asyncio
async def test_a_channel_nobody_can_read_is_not_asked_again_for_an_hour(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The all-kicked verdict changes nothing, so the identical tick must not repeat forever.

    Live evidence: 137 identical ``neurocomment_sweep_read_failed`` rows for one channel in
    11.5 hours and 27 for another — the same live ``GetFullChannelRequest`` and the same
    WARNING, 288 of each a day, none of them telling an operator anything the first did not.
    """
    await _campaign_with_authors("acc-1", "acc-2")
    reader = _patch_reader(
        monkeypatch,
        _Reader({"acc-2": _kicked(), "acc-1": _kicked("UserNotParticipantError")}),
    )

    await _sweep._sweep_once()
    await _sweep._sweep_once()

    assert reader.calls == ["acc-2", "acc-1"]  # the second tick asked Telegram nothing
    assert len(await _read_failures()) == 1  # and reported nothing new
    # And no row anywhere: the mute exists precisely because parking one would spend a
    # re-join and start the 48h countdown on a channel that may simply have gone private.
    for account_id in ("acc-1", "acc-2"):
        assert await fetch_readiness(account_id, _CHANNEL) is None


@pytest.mark.asyncio
async def test_the_mute_lapses_and_the_channel_is_tried_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A channel that went private can come back, so the silence is a deadline, not a state."""
    monkeypatch.setattr(_sweep_read, "_MUTE_FOR", timedelta(0))
    await _campaign_with_authors("acc-1", "acc-2")
    reader = _patch_reader(
        monkeypatch,
        _Reader({"acc-2": _kicked(), "acc-1": _kicked("UserNotParticipantError")}),
    )

    await _sweep._sweep_once()
    await _sweep._sweep_once()

    assert reader.calls == ["acc-2", "acc-1", "acc-2", "acc-1"]  # both authors, both ticks
    assert len(await _read_failures()) == 2


@pytest.mark.asyncio
async def test_a_read_that_works_forgets_the_mute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lapsed deadline blocks nobody, and the read that proves it is dropped, not left.

    Left behind, the entry would still be there when the channel next goes quiet — and an
    hour that started before the recovery is not the hour this verdict is owed.
    """
    await _campaign_with_authors("acc-1", "acc-2")
    _sweep_read._MUTED_UNTIL[_CHANNEL] = datetime.now(UTC) - timedelta(seconds=1)
    reader = _patch_reader(
        monkeypatch,
        _Reader({"acc-2": CheckMessagesAliveResult(missing_ids=[101])}),
    )

    await _sweep._sweep_once()

    assert reader.calls == ["acc-2"]
    assert _CHANNEL not in _sweep_read._MUTED_UNTIL
    gone = await fetch_comment(_CHANNEL, 1)
    assert gone is not None
    assert gone.deleted_at is not None  # and the deletion check ran on that answer


@pytest.mark.asyncio
async def test_a_single_kick_is_still_parked_and_never_mutes_the_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One reader out of several is the ACCOUNT talking, and that channel stays checked.

    The mute is for the verdict that parks NOBODY. A walk that parks somebody has already
    changed something, so the next tick is a different tick — and here it is the tick that
    still notices what this channel deletes.
    """
    await _campaign_with_authors("acc-1", "acc-2")
    reader = _patch_reader(
        monkeypatch,
        _Reader({"acc-2": _kicked(), "acc-1": CheckMessagesAliveResult(missing_ids=[101])}),
    )

    await _sweep._sweep_once()
    await _sweep._sweep_once()

    parked = await fetch_readiness("acc-2", _CHANNEL)
    assert parked is not None
    assert _rejoin.access_lost(parked) is True  # the re-join rule still owns it
    # Second tick: the parked author is skipped, and ``acc-1`` reads the channel as before.
    assert reader.calls == ["acc-2", "acc-1", "acc-1"]
    assert await _read_failures() == []


@pytest.mark.asyncio
async def test_a_captcha_retired_author_is_never_read_with(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_captcha_retry._give_up_and_leave`` walked this pair out of the chat — it is out.

    Reading with it spends an RPC as a non-member (the anti-ban posture this module keeps),
    Telegram answers ``UserNotParticipantError``, and if another author reads fine the row is
    overwritten with the access-loss sentinel — handing a retired pair to ``_rejoin``, which
    stamps an attempt onboarding refuses to answer, so ``attempt_owed`` never goes false.
    """
    await _campaign_with_authors("acc-1", "acc-2")
    await _captcha_retire_pair("acc-2")  # the freshest author
    before = await fetch_readiness("acc-2", _CHANNEL)
    reader = _patch_reader(
        monkeypatch,
        _Reader(
            {
                "acc-2": _kicked("UserNotParticipantError"),
                "acc-1": CheckMessagesAliveResult(missing_ids=[101]),
            },
        ),
    )

    await _sweep._sweep_once()

    assert reader.calls == ["acc-1"]  # passed over, and the walk goes on
    after = await fetch_readiness("acc-2", _CHANNEL)
    assert after == before  # the terminal captcha verdict is intact
    assert after is not None
    assert (after.captcha_gave_up, after.access_lost_reason) == (True, None)
    gone = await fetch_comment(_CHANNEL, 1)
    assert gone is not None
    assert gone.deleted_at is not None  # the check still ran on the fallback author
