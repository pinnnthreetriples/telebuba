"""The chat poll: the cursor, idempotence, and who a message belongs to."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from core.repositories import neuroshilling as repository
from core.telegram_client import TelegramReadError
from schemas.neuroshilling import NeuroshillingChatMessage, NeuroshillingStepKey
from schemas.telegram_action_results import ChatMessagePreview, ReadChatMessagesResult
from schemas.telegram_actions import ReadChatMessages
from services.neuroshilling import _autoreply, _listen, _seams
from services.neuroshilling._context import RunContext
from tests.services.neuroshilling.helpers import seed_campaign

if TYPE_CHECKING:
    from schemas.telegram_actions import TelegramReadAction

_TARGET = "@alpha"
_CHATS = {"acc-1": 555, "acc-2": 555}


class _Reader:
    """Answers each poll from a queue and records how it was asked."""

    def __init__(self, *pages: list[ChatMessagePreview]) -> None:
        self.pages = list(pages)
        self.asks: list[tuple[str, ReadChatMessages]] = []
        self.failure: TelegramReadError | None = None

    async def __call__(self, account_id: str, action: TelegramReadAction) -> ReadChatMessagesResult:
        assert isinstance(action, ReadChatMessages)
        self.asks.append((account_id, action))
        if self.failure is not None:
            raise self.failure
        return ReadChatMessagesResult(messages=self.pages.pop(0) if self.pages else [])


def _preview(
    message_id: int,
    text: str = "привет",
    *,
    outgoing: bool = False,
    sender_id: int | None = None,
) -> ChatMessagePreview:
    return ChatMessagePreview(
        message_id=message_id,
        text=text,
        outgoing=outgoing,
        sender_id=sender_id,
    )


async def _context(**overrides: Any) -> RunContext:
    fields: dict[str, Any] = {"use_chat_context": True} | overrides
    seeded = await seed_campaign(targets=_TARGET, **fields)
    campaign = await repository.fetch_campaign(seeded.campaign_id)
    assert campaign is not None
    return RunContext(
        campaign=campaign,
        run_id="run-1",
        steps=list(seeded.steps),
        by_position={},
        by_role={},
        halted=set(),
        banned={},
        banned_in={},
    )


async def _already_arrived(context: RunContext) -> None:
    """Put one row in the log, so the next poll is not the baseline one.

    The first poll of a target records what it reads and answers none of it, so a test
    about the ANSWERING has to stand after one. One row is enough: the cursor is
    ``MAX(message_id)`` and everything these tests read back is above it.
    """
    await repository.record_chat_messages(
        context.campaign.campaign_id,
        _TARGET,
        [NeuroshillingChatMessage(message_id=1, text="уже читали", is_ours=True)],
    )


@pytest.fixture
def considered(monkeypatch: pytest.MonkeyPatch) -> list[NeuroshillingChatMessage]:
    """Record what the poll offered for an answer instead of answering it."""
    seen: list[NeuroshillingChatMessage] = []

    async def _consider(
        _context: RunContext,
        _target: str,
        _chats: dict[str, int],
        message: NeuroshillingChatMessage,
    ) -> None:
        seen.append(message)

    monkeypatch.setattr(_autoreply, "consider", _consider)
    return seen


@pytest.mark.parametrize(
    ("switches", "expected"),
    [
        ({"use_chat_context": False, "reply_to_humans": False, "autoresponder": "off"}, False),
        ({"use_chat_context": True, "reply_to_humans": False, "autoresponder": "off"}, True),
        ({"use_chat_context": False, "reply_to_humans": True, "autoresponder": "off"}, True),
        (
            {"use_chat_context": False, "reply_to_humans": False, "autoresponder": "neurodialog"},
            True,
        ),
    ],
)
@pytest.mark.asyncio
async def test_any_of_the_three_switches_turns_listening_on(
    switches: dict[str, Any],
    *,
    expected: bool,
) -> None:
    context = await _context(**switches)

    assert _listen.enabled(context.campaign) is expected


@pytest.mark.asyncio
async def test_a_poll_records_each_message_once_however_far_two_pages_overlap(
    monkeypatch: pytest.MonkeyPatch,
    considered: list[NeuroshillingChatMessage],
) -> None:
    """Telegram is free to hand back a page that overlaps the previous one.

    Only the rows actually inserted are offered for an answer, so an overlap costs
    nothing rather than paying for a second model call on the same message.
    """
    reader = _Reader([_preview(7), _preview(8)], [_preview(8), _preview(9)])
    monkeypatch.setattr(_seams, "execute_read", reader)
    context = await _context()
    await _already_arrived(context)

    assert await _listen.poll_once(context, _TARGET, _CHATS) == 2
    assert await _listen.poll_once(context, _TARGET, _CHATS) == 1
    assert [message.message_id for message in considered] == [7, 8, 9]


@pytest.mark.asyncio
async def test_the_first_poll_records_the_backlog_and_answers_none_of_it(
    monkeypatch: pytest.MonkeyPatch,
    considered: list[NeuroshillingChatMessage],
) -> None:
    """``min_id=0`` asks for the newest page whatever its age, which is the backlog.

    Zero is a real cursor — the gateway reads it as "the latest page", which is where
    listening has to start rather than at the beginning of the chat — but everything
    that comes back is new to us, and in a quiet target that is a page of messages from
    last month. Answering it publishes a reply to each and buys a draft for each. The
    rows are still recorded, which is what makes the second poll's cursor mean "since
    we arrived".
    """
    reader = _Reader([_preview(7), _preview(30)], [_preview(31)])
    monkeypatch.setattr(_seams, "execute_read", reader)
    context = await _context()

    assert await _listen.poll_once(context, _TARGET, _CHATS) == 2
    assert considered == []
    assert await _listen.poll_once(context, _TARGET, _CHATS) == 1

    assert [ask.min_id for _account, ask in reader.asks] == [0, 30]
    assert [message.message_id for message in considered] == [31]


@pytest.mark.asyncio
async def test_one_account_reads_a_target_and_it_is_the_same_one_every_poll(
    monkeypatch: pytest.MonkeyPatch,
    considered: list[NeuroshillingChatMessage],
) -> None:
    """N accounts polling one chat is N times the rate limit for the same answer."""
    reader = _Reader([_preview(7)], [_preview(8)])
    monkeypatch.setattr(_seams, "execute_read", reader)
    context = await _context()
    await _already_arrived(context)

    await _listen.poll_once(context, _TARGET, _CHATS)
    await _listen.poll_once(context, _TARGET, _CHATS)

    assert [account_id for account_id, _ask in reader.asks] == ["acc-1", "acc-1"]
    assert len(considered) == 2


@pytest.mark.asyncio
async def test_a_sibling_accounts_line_is_recognised_as_ours(
    monkeypatch: pytest.MonkeyPatch,
    considered: list[NeuroshillingChatMessage],
) -> None:
    """Telethon's ``out`` flag only answers for the account doing the reading.

    A line said by another account of the same campaign arrives looking like a
    stranger's, and left at that the fleet would answer its own scripted dialogue.
    """
    reader = _Reader([_preview(7, outgoing=True), _preview(8), _preview(9)])
    monkeypatch.setattr(_seams, "execute_read", reader)
    context = await _context()
    await _already_arrived(context)
    key = NeuroshillingStepKey(run_id="run-1", target=_TARGET, step_id=context.steps[0].step_id)
    await repository.claim_message(
        key,
        campaign_id=context.campaign.campaign_id,
        account_id="acc-2",
        text="line",
    )
    await repository.settle_message(key, status="sent", message_id=8)

    await _listen.poll_once(context, _TARGET, _CHATS)

    assert [(message.message_id, message.is_ours) for message in considered] == [
        (7, True),
        (8, True),
        (9, False),
    ]


@pytest.mark.asyncio
async def test_a_line_our_own_account_wrote_is_recognised_by_its_sender(
    monkeypatch: pytest.MonkeyPatch,
    considered: list[NeuroshillingChatMessage],
) -> None:
    """The answer of last resort, and the only one an ``unconfirmed`` send leaves.

    A send whose connection died comes back with no message id at all, so there is
    nothing to write into either the journal or the chat log — and the line may well
    be in the chat regardless. Recognising the author is what stops a sibling account
    answering it.
    """
    reader = _Reader([_preview(7, sender_id=4242), _preview(8, sender_id=99)])
    monkeypatch.setattr(_seams, "execute_read", reader)
    context = (await _context())._replace(our_user_ids=frozenset({4242}))
    await _already_arrived(context)

    await _listen.poll_once(context, _TARGET, _CHATS)

    assert [(message.message_id, message.is_ours) for message in considered] == [
        (7, True),
        (8, False),
    ]


@pytest.mark.asyncio
async def test_a_poll_stops_answering_when_the_window_closes_under_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A page is twenty answers, so the deadline has to be checked between them.

    Tested only at the top of the loop, ``listen_minutes`` bounded when the last poll
    STARTED and nothing about how long it went on publishing afterwards.
    """
    elapsed = 0.0
    answered: list[int] = []

    def _clock() -> float:
        return elapsed

    async def _pause(_seconds: float) -> None:
        nonlocal elapsed
        elapsed += 1.0

    async def _consider(
        _context: RunContext,
        _target: str,
        _chats: dict[str, int],
        message: NeuroshillingChatMessage,
    ) -> None:
        nonlocal elapsed
        answered.append(message.message_id)
        elapsed += 40.0

    reader = _Reader([_preview(7), _preview(8), _preview(9)], [])
    monkeypatch.setattr(_seams, "execute_read", reader)
    monkeypatch.setattr(_seams, "monotonic", _clock)
    monkeypatch.setattr(_seams, "sleep", _pause)
    monkeypatch.setattr(_autoreply, "consider", _consider)
    context = await _context(listen_minutes=1)
    await _already_arrived(context)

    await _listen.listen(context, _TARGET, _CHATS)

    assert answered == [7, 8]
    assert await repository.chat_cursor(context.campaign.campaign_id, _TARGET) == 9


@pytest.mark.asyncio
async def test_a_failed_read_writes_nothing_and_leaves_the_cursor_where_it_was(
    monkeypatch: pytest.MonkeyPatch,
    considered: list[NeuroshillingChatMessage],
) -> None:
    """A flood or a dropped socket means "not this time", not "write it off"."""
    reader = _Reader([_preview(7)])
    reader.failure = TelegramReadError("FloodWait(30s)", kind="flood_wait", seconds=30)
    monkeypatch.setattr(_seams, "execute_read", reader)
    context = await _context()

    assert await _listen.poll_once(context, _TARGET, _CHATS) == 0
    assert considered == []
    assert await repository.chat_cursor(context.campaign.campaign_id, _TARGET) == 0


@pytest.mark.asyncio
async def test_a_target_nobody_can_read_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
    considered: list[NeuroshillingChatMessage],
) -> None:
    reader = _Reader([_preview(7)])
    monkeypatch.setattr(_seams, "execute_read", reader)
    context = await _context()
    context.halted.update(_CHATS)

    assert await _listen.poll_once(context, _TARGET, _CHATS) == 0
    assert (reader.asks, considered) == ([], [])


@pytest.mark.asyncio
async def test_a_campaign_that_asked_for_nothing_makes_no_request(
    monkeypatch: pytest.MonkeyPatch,
    considered: list[NeuroshillingChatMessage],
) -> None:
    """``listen`` returns before the first sleep, so an ordinary campaign is unchanged."""
    reader = _Reader([_preview(7)])
    monkeypatch.setattr(_seams, "execute_read", reader)
    context = await _context(use_chat_context=False)

    await _listen.listen(context, _TARGET, _CHATS)

    assert (reader.asks, considered) == ([], [])


@pytest.mark.asyncio
async def test_the_window_is_a_deadline_and_the_loop_leaves_when_it_passes(
    monkeypatch: pytest.MonkeyPatch,
    considered: list[NeuroshillingChatMessage],
) -> None:
    """``listen_minutes`` bounds the CLOCK, not a number of polls.

    The clock and the pauses are stubbed together — they are one seam — so the loop
    runs its real arithmetic against a controlled minute: two thirty-one-second
    gaps fit inside sixty seconds and the third does not.

    The second poll is entered a second before the window closes and its own sleep
    carries it past — so it reads, and stores what it read, and answers none of it.
    Reading past the deadline costs a request; publishing past it is what the
    operator's number was about.
    """
    elapsed = 0.0

    def _clock() -> float:
        return elapsed

    async def _pause(_seconds: float) -> None:
        nonlocal elapsed
        elapsed += 31.0

    reader = _Reader([_preview(7)], [_preview(8)])
    monkeypatch.setattr(_seams, "execute_read", reader)
    monkeypatch.setattr(_seams, "monotonic", _clock)
    monkeypatch.setattr(_seams, "sleep", _pause)
    context = await _context(listen_minutes=1)
    await _already_arrived(context)

    await _listen.listen(context, _TARGET, _CHATS)

    assert len(reader.asks) == 2
    assert [message.message_id for message in considered] == [7]
    assert await repository.chat_cursor(context.campaign.campaign_id, _TARGET) == 8
