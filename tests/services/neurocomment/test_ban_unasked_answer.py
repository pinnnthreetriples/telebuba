"""A postponed or refused @SpamBot answer confirms no ban and spends no refusal (D9, D14).

The gateway answers an ask it will not run with ``served_from="deferred"`` or ``"refused"``: that
is not a ``clean`` account, so the neurocomment ban ladder takes nothing from it and the repeated
unconfirmed-ban rule neither counts the refusal nor ends in the irreversible exit. The rule itself
(the budget, the interval, the exit on a clean reading) is unchanged.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import _get_engine, fetch_readiness  # type: ignore[attr-defined]
from schemas.spam_status import SpamStatusVerdict
from services.neurocomment import _seams, _state, bans
from tests.services.neurocomment.engine_support import _CommentStub, _make_campaign, _patch_io

if TYPE_CHECKING:
    from schemas.spam_intel import ServedFrom

_CHANNEL = "@chan"
_ACCOUNT = "acc-1"


class _Gateway:
    """The ``refresh_spam_status`` seam: ``clean`` while ``served_from`` is None, else unasked.

    ``forced_from`` answers the forced ask only (the exit's fresh reading); the cheaper cached
    ask keeps reading ``clean``.
    """

    def __init__(self) -> None:
        self.served_from: ServedFrom | None = None
        self.forced_from: ServedFrom | None = None
        self.asks = 0

    async def refresh_spam_status(
        self,
        account_id: str,
        *,
        force: bool = False,
    ) -> SpamStatusVerdict:
        self.asks += 1
        served_from = self.forced_from if force else self.served_from
        return SpamStatusVerdict(
            account_id=account_id,
            status="unknown" if served_from else "clean",
            checked_at=datetime.now(UTC).isoformat(),
            served_from=served_from,
            reason="no_proxy" if served_from == "refused" else None,
        )


def _counted() -> int:
    with _get_engine().connect() as connection:
        return connection.exec_driver_sql(
            "SELECT unconfirmed_bans FROM neurocomment_readiness "
            "WHERE account_id = ? AND channel = ?",
            (_ACCOUNT, _CHANNEL),
        ).scalar()


def _the_pause_expires() -> None:
    _state.reset_for_tests()
    yesterday = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
    with _get_engine().begin() as connection:
        connection.exec_driver_sql(
            "UPDATE neurocomment_readiness SET unconfirmed_ban_at = ? "
            "WHERE unconfirmed_ban_at IS NOT NULL",
            (yesterday,),
        )


async def _banned() -> bool:
    readiness = await fetch_readiness(_ACCOUNT, _CHANNEL)
    assert readiness is not None
    return readiness.banned


@pytest.fixture
def _budget_of_two(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.neurocomment, "channel_max_rounds", 2)
    monkeypatch.setattr(settings.neurocomment, "channel_pause_hours", 24.0)


@pytest.mark.asyncio
@pytest.mark.usefixtures("isolate_engine", "_budget_of_two")
@pytest.mark.parametrize("served_from", ["deferred", "refused"])
async def test_an_unasked_answer_neither_counts_a_refusal_nor_bans(
    monkeypatch: pytest.MonkeyPatch, served_from: ServedFrom
) -> None:
    await _make_campaign(_CHANNEL, _ACCOUNT)
    leave = _CommentStub()
    _patch_io(monkeypatch, comment=leave)
    gateway = _Gateway()
    monkeypatch.setattr(_seams, "refresh_spam_status", gateway.refresh_spam_status)

    gateway.served_from = served_from
    assert await bans.register_unconfirmed_ban(_ACCOUNT, _CHANNEL, known_state="can_send") is None
    assert (_counted(), await _banned()) == (0, False)  # asked, but nothing was charged

    gateway.served_from = None  # a real clean reading counts the refusal as before
    assert await bans.register_unconfirmed_ban(_ACCOUNT, _CHANNEL, known_state="can_send") == "1/2"
    _the_pause_expires()

    gateway.served_from = None
    gateway.forced_from = served_from  # counted and over budget: the exit asks, unanswered
    assert await bans.register_unconfirmed_ban(_ACCOUNT, _CHANNEL, known_state="can_send") is None
    assert await _banned() is False
    assert not [call for _, call in leave.calls if call.action_type == "leave_discussion_group"]
    assert _counted() == 2  # the refusal WAS counted; only the irreversible step was held back


@pytest.mark.asyncio
@pytest.mark.usefixtures("isolate_engine")
@pytest.mark.parametrize("served_from", ["deferred", "refused"])
async def test_the_ban_ladder_takes_no_proof_from_an_unasked_answer(
    monkeypatch: pytest.MonkeyPatch, served_from: ServedFrom
) -> None:
    from schemas.telegram_actions import BanCheckResult  # noqa: PLC0415 - one test needs it

    await _make_campaign(_CHANNEL, _ACCOUNT)
    leave = _CommentStub()
    _patch_io(monkeypatch, comment=leave)
    gateway = _Gateway()
    gateway.forced_from = served_from

    async def _read(_account_id: str, _action: object) -> BanCheckResult:
        return BanCheckResult(state="restricted")

    monkeypatch.setattr(_seams, "execute_read", _read)
    monkeypatch.setattr(_seams, "refresh_spam_status", gateway.refresh_spam_status)

    assert await bans.confirm_group_ban_and_leave(_ACCOUNT, _CHANNEL) is False
    assert await _banned() is False
    assert leave.calls == []
