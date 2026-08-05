"""Before the irreversible step, @SpamBot is asked again — not read off the cache (#47).

The repeated-unconfirmed-ban rule counts refusals on a cached verdict, which is right for a
count a delivered comment can undo. The ban it ends in is not undoable, and the two clocks do
not line up: the cache lives ``spam_status_ttl_hours`` (36h) while two counted refusals are
``channel_pause_hours`` (24h) apart, so a verdict stamped less than 12h before the FIRST
refusal is still served, unrefreshed, to the second. An account Telegram limited in between
read as clean and lost the chat for good — and, the count being per channel, every other chat
it posted to that day.

``bans._ban_on_a_spent_budget`` pays for one fresh reading there and nowhere else. These pin
that it happens, that a limited account keeps its position instead of being banned or
refunded, and that no cheaper refusal pays for a probe.

Own module because ``test_repeated_unconfirmed_ban`` sits close to the 700-line test cap.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import _get_engine, fetch_readiness, list_recent_logs  # type: ignore[attr-defined]
from schemas.spam_status import SpamStatusVerdict
from services.neurocomment import _seams, _state, bans
from tests.services.neurocomment.engine_support import _CommentStub, _make_campaign, _patch_io

if TYPE_CHECKING:
    from schemas.logs import LogEntry
    from schemas.spam_status import SpamStatusKind
    from schemas.telegram_actions import TelegramAction

_CHANNEL = "@chan"
_ACCOUNT = "acc-1"


@pytest.fixture
def _budget_of_two(monkeypatch: pytest.MonkeyPatch) -> None:
    """The rule's two inputs, pinned so retuning them cannot rewrite these tests.

    ``channel_max_rounds`` is the threshold and, with ``channel_pause_hours``, both the 48h
    window and the 24h minimum interval between two counted refusals.
    """
    monkeypatch.setattr(settings.neurocomment, "channel_max_rounds", 2)
    monkeypatch.setattr(settings.neurocomment, "channel_pause_hours", 24.0)


class _SpamBot:
    """The ``refresh_spam_status`` seam with its cache modelled, and every ask recorded.

    Two verdicts, because the defect turns entirely on them differing: a reading inside
    ``spam_status_ttl_hours`` is served from the last stamp (``cached``), and only
    ``force=True`` opens a real dialogue and learns ``fresh``. A stub with one verdict cannot
    tell the fresh probe from the cached read it was added to replace.
    """

    def __init__(
        self,
        *,
        cached: SpamStatusKind = "clean",
        fresh: SpamStatusKind = "clean",
    ) -> None:
        self.cached = cached
        self.fresh = fresh
        # The ``force`` of every reading, in order — this is what "asked again" means.
        self.asks: list[bool] = []

    async def refresh_spam_status(
        self,
        account_id: str,
        *,
        force: bool = False,
    ) -> SpamStatusVerdict:
        self.asks.append(force)
        return SpamStatusVerdict(
            account_id=account_id,
            status=self.fresh if force else self.cached,
            checked_at="2026-01-01T00:00:00",
        )


def _patch_spambot(monkeypatch: pytest.MonkeyPatch, spambot: _SpamBot) -> _SpamBot:
    monkeypatch.setattr(_seams, "refresh_spam_status", spambot.refresh_spam_status)
    return spambot


def _leaves(calls: list[tuple[str, TelegramAction]]) -> int:
    """Every ``LeaveDiscussionGroup`` the stub saw — the ban's one irreversible move."""
    return sum(1 for _, action in calls if action.action_type == "leave_discussion_group")


def _the_pause_expires() -> None:
    """The next day: the cooldown and the pair's own stamp are both behind us.

    Two clocks, because the minimum interval lives in the counting UPDATE: the in-memory
    cooldown the engine's selection gate reads, and ``unconfirmed_ban_at``, which is what the
    SQL clause compares against. Backdating the stamp is what a day looks like to it.
    """
    _state.reset_for_tests()
    yesterday = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
    with _get_engine().begin() as connection:
        connection.exec_driver_sql(
            "UPDATE neurocomment_readiness SET unconfirmed_ban_at = ? "
            "WHERE unconfirmed_ban_at IS NOT NULL",
            (yesterday,),
        )


def _counted(account_id: str = _ACCOUNT, channel: str = _CHANNEL) -> int:
    """The pair's raw ``unconfirmed_bans`` — the position a refused ban must not move.

    Read off the table: this counter is the rule's own bookkeeping and deliberately not part
    of the readiness model the board and the API are served.
    """
    with _get_engine().connect() as connection:
        return connection.exec_driver_sql(
            "SELECT unconfirmed_bans FROM neurocomment_readiness "
            "WHERE account_id = ? AND channel = ?",
            (account_id, channel),
        ).scalar()


async def _banned() -> bool:
    readiness = await fetch_readiness(_ACCOUNT, _CHANNEL)
    assert readiness is not None
    return readiness.banned


async def _lines(code: str) -> list[LogEntry]:
    return [entry for entry in await list_recent_logs(limit=200) if entry.event == code]


async def _refuse() -> str | None:
    """One unconfirmed refusal, exactly as the post path spends it."""
    return await bans.register_unconfirmed_ban(_ACCOUNT, _CHANNEL, known_state="can_send")


@pytest.mark.asyncio
@pytest.mark.usefixtures("isolate_engine", "_budget_of_two")
async def test_the_last_refusal_does_not_ban_an_account_telegram_limited_in_between(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The window the cache left open: clean when the count started, limited when it ended.

    An account-wide write restriction is refused in every chat it posts to, which is why the
    rule charges it to nobody — but on the last refusal the verdict it was charging came from
    a stamp up to 36h old. So the pair stays in the chat, keeps its position, and the operator
    gets the line that says which of the two things happened.
    """
    await _make_campaign(_CHANNEL, _ACCOUNT)
    leave = _CommentStub()  # the ban's exit rides this seam, and must not reach it
    _patch_io(monkeypatch, comment=leave)
    _patch_spambot(monkeypatch, _SpamBot(cached="clean", fresh="limited"))

    assert await _refuse() == "1/2"
    _the_pause_expires()

    assert await _refuse() is None  # counted, but the ban is refused → no position to report

    assert await _banned() is False
    assert _leaves(leave.calls) == 0
    # Nothing is spent back either: the refusal WAS counted, and pretending otherwise would
    # hand a limited account a fresh budget on every chat it is refused in.
    assert _counted() == 2
    limited = await _lines("neurocomment_group_ban_account_limited")
    assert len(limited) == 1
    assert limited[0].account_id == _ACCOUNT
    assert limited[0].extra["channel"] == _CHANNEL
    assert limited[0].extra["state"] == "can_send"
    assert limited[0].extra["unconfirmed_bans"] == 2
    assert await _lines("neurocomment_account_banned") == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("isolate_engine", "_budget_of_two")
async def test_the_pair_is_banned_on_its_next_refusal_once_the_account_is_clean_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The re-check defers the verdict, it does not cancel it.

    A limited account is re-judged on its next refusal, and by then the group has refused a
    healthy account three times inside the window — so the exit lands. The label still closes
    the run at the budget: an over-run must not render as "3/2".
    """
    await _make_campaign(_CHANNEL, _ACCOUNT)
    leave = _CommentStub()
    _patch_io(monkeypatch, comment=leave)
    spambot = _patch_spambot(monkeypatch, _SpamBot(cached="clean", fresh="limited"))

    assert await _refuse() == "1/2"
    _the_pause_expires()
    assert await _refuse() is None  # the fresh probe said limited
    _the_pause_expires()
    spambot.fresh = "clean"  # @SpamBot has let the account go

    assert await _refuse() == "2/2"

    assert await _banned() is True
    assert _leaves(leave.calls) == 1
    banned = await _lines("neurocomment_account_banned")
    assert len(banned) == 1
    assert banned[0].extra["reason"] == "2/2"
    assert banned[0].extra["unconfirmed_bans"] == 3  # the raw count, over the clamped label


@pytest.mark.asyncio
@pytest.mark.usefixtures("isolate_engine", "_budget_of_two")
async def test_only_the_irreversible_step_pays_for_a_fresh_spambot_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One forced probe per pair per budget, and none at all on a refusal that costs nothing.

    Asked per refusal, a probe past its TTL opens a real @SpamBot dialogue on the post hot
    path — and a failed one is not cached, so a struggling account would repeat it every time.
    So the order is load-bearing: the interval turns a refusal away before any reading, a
    counted refusal below the budget takes the cached one, and only the refusal that would
    take the chat away pays for the truth.
    """
    await _make_campaign(_CHANNEL, _ACCOUNT)
    _patch_io(monkeypatch, comment=_CommentStub())
    spambot = _patch_spambot(monkeypatch, _SpamBot())

    assert await _refuse() == "1/2"
    assert spambot.asks == [False]  # counted, but nowhere near the exit → the cache will do

    assert await _refuse() is None  # inside the interval
    assert spambot.asks == [False]  # ...and charged nobody, so it asked nobody

    _the_pause_expires()

    assert await _refuse() == "2/2"
    # The cached read that counts the refusal, then the forced one the ban is gated on.
    assert spambot.asks == [False, False, True]
