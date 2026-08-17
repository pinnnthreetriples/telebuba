"""What the gateway's answers mean to a campaign: joins, resolves and refusals."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from core.repositories.neurocomment import count_account_joins_since, record_join
from core.repositories.neuroshilling import (
    create_campaign,
    list_presence,
    record_presence,
    retire_account_presence,
)
from core.telegram_client import UNCONFIRMED_ERROR_TYPE, TelegramReadError
from schemas.neuroshilling import NeuroshillingCampaignCreate
from schemas.telegram_actions import ActionResult, ResolveChatResult
from services import pacing
from services.neuroshilling import _seams, _telegram

_PAST = "1970-01-01T00:00:00+00:00"


def _result(status: str, **fields: Any) -> ActionResult:
    return ActionResult(
        status=status,  # ty: ignore[invalid-argument-type]
        action_type="join_channel",
        account_id="acc-1",
        **fields,
    )


@pytest.fixture
def paced(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, float]]:
    """Record every pacer slot instead of actually sleeping 30-120 seconds for it."""
    seen: list[tuple[str, float]] = []

    async def fake_slot(key: str, gap: float) -> None:
        seen.append((key, gap))

    monkeypatch.setattr(pacing, "await_send_slot", fake_slot)
    return seen


async def _campaign() -> str:
    created = await create_campaign(NeuroshillingCampaignCreate(name="Promo"))
    return created.campaign_id


@pytest.mark.parametrize(
    ("result", "verdict"),
    [
        (_result("ok"), "sent"),
        (_result("already_participant"), "sent"),
        # The rate-limit family carries NO error_type at all, so a caller that only
        # switches on error_type sees nothing and keeps posting.
        (_result("flood_wait", flood_wait_seconds=60), "halt"),
        (_result("peer_flood"), "halt"),
        # A 420 counted against the ACCOUNT, so skipping to the next chat would spend
        # the same refused budget — warming halts on it and so does this.
        (_result("premium_wait", flood_wait_seconds=10), "halt"),
        (_result("slow_mode_wait", flood_wait_seconds=10), "chat_wait"),
        (_result("unavailable", error_type=UNCONFIRMED_ERROR_TYPE), "unconfirmed"),
        (_result("failed", error_type="UserBannedInChannelError"), "account_banned"),
        # The dead-session family reaches us under ONE wrapper class, so the class name
        # says nothing and the stable code in ``error_message`` is the whole answer.
        # ``UserDeactivatedBanError`` is an ``UnauthorizedError``: it can never arrive
        # as itself.
        *(
            (
                _result("failed", error_type="ProfileGatewayError", error_message=code),
                "account_dead",
            )
            for code in ("account_deactivated", "account_frozen", "session_dead")
        ),
        # A property of the CHAT: a substitute account meets the identical wall.
        (_result("failed", error_type="ChatWriteForbiddenError"), "chat_blocked"),
        (_result("failed", error_type="ChatSendPlainForbiddenError"), "chat_blocked"),
        (_result("failed", error_type="ChatSendMediaForbiddenError"), "chat_blocked"),
        (_result("failed", error_type="ChatRestrictedError"), "chat_blocked"),
        # Private or banned — Telethon's text covers both, so a ``not_member`` verdict
        # would send a re-join looping against a ban that answers the same way forever.
        (_result("failed", error_type="ChannelPrivateError"), "chat_unavailable"),
        (_result("failed", error_type="UserNotParticipantError"), "not_member"),
        # How an unknown peer reaches us: the session entity cache is per account.
        (_result("failed", error_type="ValueError"), "not_member"),
        (_result("failed", error_type="RandomError"), "failed"),
        (_result("unavailable", error_type="ConnectionError"), "failed"),
    ],
)
def test_a_send_outcome_says_whose_fault_it_is(result: ActionResult, verdict: str) -> None:
    assert _telegram.classify_send(result) == verdict


@pytest.mark.parametrize(
    ("result", "state"),
    [
        (_result("ok"), "joined"),
        # Already inside is a SUCCESS with a non-ok status; reading the status alone
        # would fail a pair that is in fact in the chat.
        (_result("already_participant"), "joined"),
        # Deliberately reported as ``failed`` by the gateway — the account is NOT in.
        (_result("failed", error_type="InviteRequestSentError"), "pending_approval"),
        (_result("failed", error_type="ChannelsTooMuchError"), "retired"),
        # The dead-session family, arriving on the JOIN under the same wrapper class it
        # arrives under on the send: the class name is the wrapper's, so the stable code
        # in ``error_message`` is the only thing that says which of the three it was.
        *(
            (_result("failed", error_type="ProfileGatewayError", error_message=code), "retired")
            for code in ("account_deactivated", "account_frozen", "session_dead")
        ),
        (_result("flood_wait", flood_wait_seconds=60), "flooded"),
        (_result("peer_flood"), "flooded"),
        (_result("premium_wait", flood_wait_seconds=30), "flooded"),
        (_result("slow_mode_wait", flood_wait_seconds=5), "pending"),
        (_result("unavailable", error_type="ConnectionError"), "pending"),
        (_result("failed", error_type="InviteHashExpiredError"), "refused"),
    ],
)
def test_a_join_outcome_maps_to_the_pairs_presence(result: ActionResult, state: str) -> None:
    assert _telegram.classify_join(result) == state


@pytest.mark.asyncio
async def test_a_join_is_spaced_charged_and_recorded(
    monkeypatch: pytest.MonkeyPatch,
    paced: list[tuple[str, float]],
) -> None:
    campaign_id = await _campaign()
    monkeypatch.setattr(_seams, "execute", _fake_execute(_result("ok")))

    state = await _telegram.join_target(campaign_id, "acc-1", "+HASH")

    assert state == "joined"
    # Its own pacer key, so joins are serialised per account independently of sends
    # however many targets are in flight.
    assert [key for key, _gap in paced] == ["join:acc-1"]
    assert 30.0 <= paced[0][1] <= 120.0
    assert await count_account_joins_since("acc-1", _PAST) == 1
    rows = await list_presence(campaign_id)
    assert [(row.account_id, row.target, row.state) for row in rows] == [
        ("acc-1", "+HASH", "joined"),
    ]


@pytest.mark.asyncio
async def test_an_already_participant_join_is_not_charged_to_the_daily_cap(
    monkeypatch: pytest.MonkeyPatch,
    paced: list[tuple[str, float]],
) -> None:
    """A no-op re-join must not spend budget, or the counter pins near the cap.

    Every restart re-issues the same joins, so charging them would starve the joins
    that actually matter — the rule the neurocomment listener pass already applies to
    this very table.
    """
    campaign_id = await _campaign()
    monkeypatch.setattr(_seams, "execute", _fake_execute(_result("already_participant")))

    assert await _telegram.join_target(campaign_id, "acc-1", "@group") == "joined"
    assert await count_account_joins_since("acc-1", _PAST) == 0
    assert paced


@pytest.mark.asyncio
async def test_the_daily_cap_is_counted_in_the_log_neurocomment_shares(
    monkeypatch: pytest.MonkeyPatch,
    paced: list[tuple[str, float]],
) -> None:
    """Telegram counts joins per ACCOUNT and does not care which feature spent them.

    A private counter would let one account join twice its budget with both features
    certain they had stayed inside it — so the campaign reads neurocomment's log, and
    nothing is dispatched or written once it is full.
    """
    campaign_id = await _campaign()
    monkeypatch.setattr(_seams, "execute", _fake_execute(_result("ok")))
    for _ in range(20):
        await record_join("acc-1")

    assert await _telegram.join_target(campaign_id, "acc-1", "@group") == "pending"
    assert paced == []
    assert await list_presence(campaign_id) == []


@pytest.mark.asyncio
async def test_concurrent_joins_for_one_account_still_stop_at_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pacer is a QUEUE, not a mutex over the cap.

    Checked only before entering it, all six of these joins read a count that no join
    had yet incremented, every one of them passed, and the account made six joins
    against a cap of two — spaced out, and entirely uncapped. The count is therefore
    re-read under the per-account join mutex, which is held from that read until the
    join it authorises has been charged.

    The stubbed RPC is twenty times the gap on purpose. A stub that returned at once
    closed the window by itself, so this case passed against the unlocked code and only
    reddened when the machine was slow enough to reopen it.
    """
    from core.config import settings  # noqa: PLC0415 - patched per test, not at import

    monkeypatch.setattr(settings.neuroshilling, "max_joins_per_account_per_day", 2)
    # The real pacer, not the fixture: it is what puts these six in a queue at all.
    monkeypatch.setattr(_telegram, "_join_gap_seconds", lambda: 0.01)
    campaign_id = await _campaign()
    joins: list[str] = []

    async def slow_join(account_id: str, action: Any) -> ActionResult:  # noqa: ARG001
        joins.append(action.channel)
        await asyncio.sleep(0.2)
        return _result("ok")

    monkeypatch.setattr(_seams, "execute", slow_join)

    states = await asyncio.gather(
        *(_telegram.join_target(campaign_id, "acc-1", f"@t{index}") for index in range(6)),
    )

    assert await count_account_joins_since("acc-1", _PAST) == 2
    assert sorted(states) == ["joined", "joined", *["pending"] * 4]
    # And the four refused ones cost no Telegram traffic: the mutex is taken before the
    # count is read, not merely around the write.
    assert len(joins) == 2


@pytest.mark.asyncio
async def test_a_disabled_cap_lets_the_join_through(
    monkeypatch: pytest.MonkeyPatch,
    paced: list[tuple[str, float]],
) -> None:
    from core.config import settings  # noqa: PLC0415 - patched per test, not at import

    monkeypatch.setattr(settings.neuroshilling, "max_joins_per_account_per_day", 0)
    campaign_id = await _campaign()
    monkeypatch.setattr(_seams, "execute", _fake_execute(_result("ok")))
    for _ in range(50):
        await record_join("acc-1")

    assert await _telegram.join_target(campaign_id, "acc-1", "@group") == "joined"
    assert paced


@pytest.mark.asyncio
async def test_a_join_request_leaves_the_account_outside(
    monkeypatch: pytest.MonkeyPatch,
    paced: list[tuple[str, float]],  # noqa: ARG001 - the fixture only suppresses the sleep
) -> None:
    """The one outcome that reads like a success and is not.

    Playing the dialogue here would fail every step and burn the whole reserve on a
    single target.
    """
    campaign_id = await _campaign()
    monkeypatch.setattr(
        _seams,
        "execute",
        _fake_execute(_result("failed", error_type="InviteRequestSentError")),
    )

    state = await _telegram.join_target(campaign_id, "acc-1", "+HASH")

    assert state == "pending_approval"
    assert (await list_presence(campaign_id))[0].last_error_type == "InviteRequestSentError"
    # Charged all the same: Telegram rate-limits join REQUESTS too, and spraying them
    # at gated chats is a recognised freeze trigger — free of the cap, one account
    # could fire an unlimited number of them.
    assert await count_account_joins_since("acc-1", _PAST) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "state"),
    [
        (_result("failed", error_type="ChannelsTooMuchError"), "retired"),
        (_result("flood_wait", flood_wait_seconds=300), "flooded"),
        (
            _result("failed", error_type="ProfileGatewayError", error_message="account_frozen"),
            "retired",
        ),
    ],
)
async def test_an_account_level_refusal_retires_every_target_it_was_playing(
    monkeypatch: pytest.MonkeyPatch,
    paced: list[tuple[str, float]],  # noqa: ARG001 - the fixture only suppresses the sleep
    result: ActionResult,
    state: str,
) -> None:
    campaign_id = await _campaign()
    await record_presence(campaign_id, "acc-1", "@other", "joined")
    await record_presence(campaign_id, "acc-2", "@other", "joined")
    monkeypatch.setattr(_seams, "execute", _fake_execute(result))

    assert await _telegram.join_target(campaign_id, "acc-1", "@group") == state

    states = {(row.account_id, row.target): row.state for row in await list_presence(campaign_id)}
    assert states == {
        ("acc-1", "@group"): state,
        ("acc-1", "@other"): state,
        ("acc-2", "@other"): "joined",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("stored", ["joined", "pending_approval", "flooded", "retired"])
async def test_a_pair_already_settled_is_not_joined_again(
    monkeypatch: pytest.MonkeyPatch,
    paced: list[tuple[str, float]],
    stored: str,
) -> None:
    """The stored row is read, or persisting it bought nothing at all.

    Unread, a ``joined`` pair was re-joined after every restart and a flooded account
    was re-joined on the very next target — the two things the table exists to stop.
    """
    campaign_id = await _campaign()
    await record_presence(campaign_id, "acc-1", "@group", stored)  # ty: ignore[invalid-argument-type]
    monkeypatch.setattr(_seams, "execute", _fake_execute(_result("ok")))

    assert await _telegram.join_target(campaign_id, "acc-1", "@group") == stored
    assert paced == []
    assert await count_account_joins_since("acc-1", _PAST) == 0


@pytest.mark.asyncio
async def test_a_dead_session_on_the_join_is_not_walked_into_the_next_target(
    monkeypatch: pytest.MonkeyPatch,
    paced: list[tuple[str, float]],
) -> None:
    """A logged-out, frozen or deactivated session cannot join anything, ever.

    Read off ``error_type`` alone the three of them are one wrapper class that matches
    no branch, so the join was filed ``refused`` — the pair's verdict on one attempt,
    which no sweep clears and the settled-state gate does not stop. The account was
    handed the next target, and the next, spending a paced join slot on each for a
    session Telegram had already closed.
    """
    campaign_id = await _campaign()
    dead = _result("failed", error_type="ProfileGatewayError", error_message="session_dead")
    monkeypatch.setattr(_seams, "execute", _fake_execute(dead))

    assert await _telegram.join_target(campaign_id, "acc-1", "@first") == "retired"
    assert await _telegram.join_target(campaign_id, "acc-1", "@second") == "retired"

    # One slot spent, not one per target, and the second target was never attempted.
    assert [key for key, _gap in paced] == ["join:acc-1"]
    assert [row.target for row in await list_presence(campaign_id)] == ["@first"]


@pytest.mark.asyncio
async def test_a_pair_that_outlived_its_flood_is_not_joined_again(
    monkeypatch: pytest.MonkeyPatch,
    paced: list[tuple[str, float]],
) -> None:
    """The flood took the pair's ``joined`` row; the expiry must not take its membership.

    Answered ``None`` once the window passed, the pair looked unknown and the account
    went to re-join a chat it was already in — which is what the shared daily budget
    then ran out on, skipping every target it was a member of.
    """
    campaign_id = await _campaign()
    await record_presence(campaign_id, "acc-1", "@group", "joined")
    await retire_account_presence("acc-1", "flooded")
    monkeypatch.setattr(_telegram, "flood_since", lambda: "9999-01-01T00:00:00+00:00")
    monkeypatch.setattr(_seams, "execute", _fake_execute(_result("ok")))

    assert await _telegram.join_target(campaign_id, "acc-1", "@group") == "joined"
    assert paced == []
    assert await count_account_joins_since("acc-1", _PAST) == 0


@pytest.mark.asyncio
async def test_a_refused_pair_is_tried_again(
    monkeypatch: pytest.MonkeyPatch,
    paced: list[tuple[str, float]],  # noqa: ARG001 - the fixture only suppresses the sleep
) -> None:
    """``refused`` is the pair's verdict on ONE attempt, not a verdict on the account.

    An invite that had expired may have been replaced, and the next pass is entitled
    to find out.
    """
    campaign_id = await _campaign()
    await record_presence(campaign_id, "acc-1", "+HASH", "refused", error_type="InviteHashExpired")
    monkeypatch.setattr(_seams, "execute", _fake_execute(_result("ok")))

    assert await _telegram.join_target(campaign_id, "acc-1", "+HASH") == "joined"


@pytest.mark.asyncio
async def test_a_halted_account_does_not_walk_on_to_the_next_target(
    monkeypatch: pytest.MonkeyPatch,
    paced: list[tuple[str, float]],
) -> None:
    """The retirement sweep can only stamp rows that exist; the next target has none."""
    campaign_id = await _campaign()
    await record_presence(campaign_id, "acc-1", "@joined-earlier", "flooded")
    monkeypatch.setattr(_seams, "execute", _fake_execute(_result("ok")))

    assert await _telegram.join_target(campaign_id, "acc-1", "@never-seen") == "flooded"
    assert paced == []


@pytest.mark.asyncio
async def test_a_flood_mid_dialogue_is_written_down_like_a_flood_on_the_join() -> None:
    """Classifying it was never the problem — nothing PERSISTED it.

    The run halted, the process restarted, and the account resumed posting inside its
    own flood window against a presence table that still read ``joined``.
    """
    campaign_id = await _campaign()
    await record_presence(campaign_id, "acc-1", "@a", "joined")
    await record_presence(campaign_id, "acc-1", "@b", "joined")

    verdict = await _telegram.record_send_verdict(
        campaign_id,
        "acc-1",
        "@a",
        _result("flood_wait", flood_wait_seconds=300),
    )

    assert verdict == "halt"
    states = {row.target: row.state for row in await list_presence(campaign_id)}
    assert states == {"@a": "flooded", "@b": "flooded"}


@pytest.mark.parametrize(
    "outcome",
    [
        {"status": "failed", "error_message": "account_deactivated"},
        {"status": "failed", "error_type": "UserBannedInChannelError"},
    ],
)
@pytest.mark.asyncio
async def test_an_account_that_cannot_act_at_all_is_written_down_as_retired(
    outcome: dict[str, str],
) -> None:
    """The run's halt set held these two and nothing else did.

    A restart therefore offered a deactivated or banned account the very next target,
    which is the one case waiting cannot fix — hence ``retired``, the state that does
    not expire, rather than the flood's window.
    """
    campaign_id = await _campaign()
    await record_presence(campaign_id, "acc-1", "@a", "joined")
    await record_presence(campaign_id, "acc-1", "@b", "joined")

    verdict = await _telegram.record_send_verdict(
        campaign_id,
        "acc-1",
        "@a",
        _result(**outcome),
    )

    assert verdict in {"account_dead", "account_banned"}
    states = {row.target: row.state for row in await list_presence(campaign_id)}
    assert states == {"@a": "retired", "@b": "retired"}


@pytest.mark.asyncio
async def test_a_chat_scoped_send_refusal_writes_nothing() -> None:
    """Only the ACCOUNT verdicts are persisted.

    A chat that forbids writing is the step's problem, and retiring the account over
    it would spend a reserve on a chat setting.
    """
    campaign_id = await _campaign()

    verdict = await _telegram.record_send_verdict(
        campaign_id,
        "acc-1",
        "@a",
        _result("failed", error_type="ChatWriteForbiddenError"),
    )

    assert verdict == "chat_blocked"
    assert await list_presence(campaign_id) == []


def _fake_execute(result: ActionResult) -> Any:
    async def execute(account_id: str, action: Any) -> ActionResult:  # noqa: ARG001
        return result

    return execute


def _fake_read(outcome: Any) -> Any:
    async def execute_read(account_id: str, action: Any) -> Any:  # noqa: ARG001
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return execute_read


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["megagroup", "channel"])
async def test_a_usable_target_hands_back_this_accounts_own_chat_id(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    campaign_id = await _campaign()
    resolved = ResolveChatResult(chat_id=777, kind=kind)  # ty: ignore[invalid-argument-type]
    monkeypatch.setattr(_seams, "execute_read", _fake_read(resolved))

    assert await _telegram.resolve_target(campaign_id, "acc-1", "@group") == resolved
    assert await list_presence(campaign_id) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["basic_group", "user"])
async def test_a_shared_id_sequence_target_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    """Basic groups and private chats number messages PER USER.

    Account A's ``msg_id`` is not account B's, so the scripted reply chain would aim
    at the wrong messages without a single error to show for it.
    """
    campaign_id = await _campaign()
    monkeypatch.setattr(
        _seams,
        "execute_read",
        _fake_read(ResolveChatResult(chat_id=42, kind=kind)),  # ty: ignore[invalid-argument-type]
    )

    assert await _telegram.resolve_target(campaign_id, "acc-1", "@group") is None
    row = (await list_presence(campaign_id))[0]
    assert (row.state, row.last_error_type) == ("refused", "target_is_basic_group")


@pytest.mark.asyncio
async def test_an_unreachable_target_writes_the_pair_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign_id = await _campaign()
    monkeypatch.setattr(
        _seams,
        "execute_read",
        _fake_read(TelegramReadError("chat_not_found")),
    )

    assert await _telegram.resolve_target(campaign_id, "acc-1", "+HASH") is None
    row = (await list_presence(campaign_id))[0]
    assert (row.state, row.last_error_type) == ("refused", "chat_not_found")


@pytest.mark.asyncio
async def test_a_flood_on_the_resolve_is_the_accounts_verdict_not_the_pairs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``refused`` is PERMANENT — the retirement sweep skips it, so nothing ever clears it.

    Recorded for every read failure alike, a flood wait that was over in five minutes
    wrote the pair off for good, and the same account kept resolving the next target
    while Telegram was still counting.
    """
    campaign_id = await _campaign()
    await record_presence(campaign_id, "acc-1", "@other", "joined")
    monkeypatch.setattr(
        _seams,
        "execute_read",
        _fake_read(TelegramReadError("flood_wait", kind="flood_wait", seconds=300)),
    )

    assert await _telegram.resolve_target(campaign_id, "acc-1", "+HASH") is None
    states = {row.target: row.state for row in await list_presence(campaign_id)}
    assert states == {"+HASH": "flooded", "@other": "flooded"}


@pytest.mark.asyncio
async def test_a_gateway_outage_leaves_the_pair_exactly_as_it_was(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dead socket is not evidence about a chat, so it must not overwrite what is."""
    campaign_id = await _campaign()
    await record_presence(campaign_id, "acc-1", "+HASH", "joined")
    monkeypatch.setattr(
        _seams,
        "execute_read",
        _fake_read(TelegramReadError("pool_error", kind="unavailable")),
    )

    assert await _telegram.resolve_target(campaign_id, "acc-1", "+HASH") is None
    assert (await list_presence(campaign_id))[0].state == "joined"
