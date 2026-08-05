"""What the operator reads about the captcha solver: the attempts, and how they ended.

The solver used to log nothing at all. Its whole output was the audit row behind the
"needs check" queue, which records one ``give_up`` for three very different endings — the
model never answered, the model said it cannot, the answer was screened out — so an
operator looking at the queue could not tell a captcha that is too hard from a provider
that is not answering, and could not see how much of the attempt budget was spent.

These tests pin both halves of the fix: the counter appears only on an attempt that really
spent budget, and the ending is named in a line of its own.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import list_recent_logs
from schemas.gemini import GeminiResult
from services.neurocomment import _challenge_log, _seams, challenge
from tests.services.neurocomment.challenge_support import (
    _challenge_rows,
    _decision_text,
    _ExecuteStub,
    _gemini,
    _msg,
    _wait,
)

if TYPE_CHECKING:
    from schemas.challenge import BotChallengeMessage

pytestmark = pytest.mark.usefixtures("isolate_challenge")

# A decision the solver is willing to send: click the first button of ``_msg()``.
_CLICK = _decision_text(action="click_button", button_index=0)


async def _journal() -> list[tuple[str, str, object]]:
    """The solver's own lines, oldest first: ``(level, event, extra["reason"])``."""
    return [
        (entry.level, entry.event, entry.extra.get("reason"))
        for entry in reversed(await list_recent_logs(limit=50))
        if entry.event.startswith("neurocomment_challenge_")
    ]


def _solver(
    monkeypatch: pytest.MonkeyPatch,
    *,
    result: GeminiResult,
    challenges: tuple[BotChallengeMessage | None, ...],
    dispatch_ok: bool = True,
) -> _ExecuteStub:
    """Wire the three seams the solver touches; ``challenges`` is the wait queue."""
    execute = _ExecuteStub(ok=dispatch_ok)
    monkeypatch.setattr(_seams, "execute_read", _wait(*challenges))
    monkeypatch.setattr(_seams, "generate_text", _gemini(result))
    monkeypatch.setattr(_seams, "execute", execute.execute)
    return execute


# --------------------------------------------------------------------------- #
# The attempt line: one per answer actually sent, carrying its place in the budget.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_captcha_passed_first_try_shows_the_attempt_and_the_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _solver(monkeypatch, result=GeminiResult(status="ok", text=_CLICK), challenges=(_msg(),))

    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "solved"

    assert await _journal() == [
        ("INFO", "neurocomment_challenge_attempt", "1/2"),
        ("INFO", "neurocomment_challenge_result", _challenge_log.PASSED_REASON),
    ]


@pytest.mark.asyncio
async def test_a_second_answer_that_works_says_it_was_the_second(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The re-challenged pair: the counter is the only place this shows up as 2/2."""
    _solver(
        monkeypatch,
        result=GeminiResult(status="ok", text=_CLICK),
        challenges=(_msg(), _msg()),
    )

    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "solved"

    assert await _journal() == [
        ("INFO", "neurocomment_challenge_attempt", "1/2"),
        ("INFO", "neurocomment_challenge_attempt", "2/2"),
        ("INFO", "neurocomment_challenge_result", _challenge_log.PASSED_REASON),
    ]


@pytest.mark.asyncio
async def test_a_bot_that_keeps_asking_counts_out_the_budget_then_names_the_ending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _solver(
        monkeypatch,
        result=GeminiResult(status="ok", text=_CLICK),
        challenges=(_msg(), _msg(), _msg()),
    )

    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "failed"

    assert await _journal() == [
        ("INFO", "neurocomment_challenge_attempt", "1/2"),
        ("INFO", "neurocomment_challenge_attempt", "2/2"),
        ("WARNING", "neurocomment_challenge_result", _challenge_log.WRONG_ANSWER_REASON),
    ]


@pytest.mark.asyncio
async def test_the_counter_names_the_budget_the_operator_actually_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``M`` is read from settings, not baked in — one attempt means "1/1", never "1/2"."""
    monkeypatch.setattr(settings.neurocomment, "challenge_max_attempts", 1)
    _solver(
        monkeypatch,
        result=GeminiResult(status="ok", text=_CLICK),
        challenges=(_msg(), _msg()),
    )

    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "failed"

    assert await _journal() == [
        ("INFO", "neurocomment_challenge_attempt", "1/1"),
        ("WARNING", "neurocomment_challenge_result", _challenge_log.WRONG_ANSWER_REASON),
    ]


# --------------------------------------------------------------------------- #
# The endings: one audit ``give_up``, three different things to do about it.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_model_that_never_answered_says_so_and_spends_no_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The live-DB case: a ``give_up`` row with an empty decision explained nothing.

    Nothing was sent to the bot, so there is no spent attempt to number — the ending is
    the whole line, and it points at the provider rather than at the captcha.
    """
    execute = _solver(
        monkeypatch,
        result=GeminiResult(status="ok", text=None),  # 200, no candidates
        challenges=(_msg(),),
    )

    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "give_up"

    assert execute.calls == []
    assert await _journal() == [
        ("WARNING", "neurocomment_challenge_result", _challenge_log.NO_ANSWER_REASON),
    ]
    assert [row["outcome"] for row in _challenge_rows()] == ["give_up"]


@pytest.mark.asyncio
async def test_a_captcha_the_model_cannot_read_is_a_different_line_entirely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same ``give_up`` row as the silent model, opposite meaning for the operator."""
    _solver(
        monkeypatch,
        result=GeminiResult(status="ok", text=_decision_text(action="give_up")),
        challenges=(_msg(),),
    )

    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "give_up"

    assert await _journal() == [
        ("WARNING", "neurocomment_challenge_result", _challenge_log.UNSOLVABLE_REASON),
    ]
    assert [row["outcome"] for row in _challenge_rows()] == ["give_up"]


@pytest.mark.asyncio
async def test_an_answer_the_safety_gate_refused_says_the_answer_was_the_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A payment-looking button is never clicked — and the feed says why, not just that."""
    _solver(
        monkeypatch,
        result=GeminiResult(status="ok", text=_CLICK),
        challenges=(_msg(button_labels=["pay now", "no"]),),
    )

    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "give_up"

    assert await _journal() == [
        ("WARNING", "neurocomment_challenge_result", _challenge_log.UNSAFE_ANSWER_REASON),
    ]


@pytest.mark.asyncio
async def test_an_answer_telegram_would_not_take_is_told_apart_from_a_wrong_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The attempt was spent (we committed to the answer), the send is what failed."""
    _solver(
        monkeypatch,
        result=GeminiResult(status="ok", text=_CLICK),
        challenges=(_msg(),),
        dispatch_ok=False,
    )

    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "failed"

    assert await _journal() == [
        ("INFO", "neurocomment_challenge_attempt", "1/2"),
        ("WARNING", "neurocomment_challenge_result", _challenge_log.NOT_SENT_REASON),
    ]


@pytest.mark.asyncio
async def test_a_rate_limited_provider_is_reported_without_blaming_the_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 429 costs the pair nothing: INFO, no counter, and no audit row to clean up."""
    _solver(
        monkeypatch,
        result=GeminiResult(status="rate_limited", error="429"),
        challenges=(_msg(),),
    )

    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "rate_limited"

    assert await _journal() == [
        ("INFO", "neurocomment_challenge_result", _challenge_log.RATE_LIMITED_REASON),
    ]
    assert _challenge_rows() == []


@pytest.mark.asyncio
async def test_a_group_with_no_captcha_at_all_stays_out_of_the_feed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Most onboardings meet no guardian bot; those must not cost a line each."""
    _solver(monkeypatch, result=GeminiResult(status="ok", text=_CLICK), challenges=(None,))

    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "no_challenge"

    assert await _journal() == []
