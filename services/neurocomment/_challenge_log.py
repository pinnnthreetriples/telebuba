"""What the operator sees of the captcha solver: each attempt spent, and how it ended.

Split out of ``challenge`` for the file-size budget. Before this module the solver wrote
no journal line at all — the only trace was the audit row behind the "needs check" queue,
so a pair could burn its whole attempt budget with the feed silent, and the ``give_up``
rows carrying an empty decision (six of them in the live DB) were indistinguishable from
a captcha the model read and answered "I cannot".

Two lines, which is all a rule that fires once per pair at onboarding may cost:

* :func:`log_attempt`, written only when an answer is about to actually go to the bot,
  carrying its position in the budget in ``extra["reason"]`` — ``eventReason`` prints an
  unmapped code verbatim beside the label, so "· 1/2" costs no translation (the same
  trick the re-join, join-request and channel-pause budgets use). A decision we refuse to
  send, and a 429 that never produced one, spend no attempt and so print no counter;
* :func:`log_result`, which spends ``reason`` on the OUTCOME instead, because that is the
  half the audit row cannot express: it says ``give_up`` both when the model never
  answered and when it answered that it cannot, and the operator's next move differs
  ("the model is not answering" is a key/quota problem, "it cannot read this captcha" is
  a channel to solve by hand or leave alone).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.config import settings
from core.logging import log_event

if TYPE_CHECKING:
    from schemas.challenge import ChallengeDecision

# Outcome codes for the result line's ``reason`` (translated under ``logEventReason``).
# The ``*_REASON`` names are load-bearing, not decoration: the value reaches ``log_event``
# through a parameter, which no literal scan can follow, and
# ``tests.test_logevent_i18n_parity`` enumerates literals bound to a ``reason``/``*_reason``
# NAME — the same reason ``_generate._CLAIM_LOST_REASON`` is spelled that way.
PASSED_REASON = "captcha_passed"
NO_ANSWER_REASON = "captcha_no_answer"
UNSOLVABLE_REASON = "captcha_unsolvable"
UNSAFE_ANSWER_REASON = "captcha_unsafe_answer"
NOT_SENT_REASON = "captcha_not_sent"
WRONG_ANSWER_REASON = "captcha_wrong_answer"
RATE_LIMITED_REASON = "llm_rate_limited"

# The two outcomes that ask nothing of the operator: the pair is through, or the provider's
# 429 defers it for an un-penalized retry. Every other outcome parks the pair in the
# "needs check" queue, which is a decision waiting on a human — hence WARNING.
_INFO_REASONS = frozenset({PASSED_REASON, RATE_LIMITED_REASON})


def refusal(decision: ChallengeDecision | None, *, unsafe: bool) -> str:
    """Which of the three give-ups this is — the ones the audit row flattens into one.

    Either no usable answer came back at all (timeout, unparseable body, confidence below
    the floor, unusable key), or the model answered that it cannot solve this one, or the
    answer was screened out by the outbound gate as a phishing / payment trap. Called only
    once the solver has decided not to answer, so there is no "we will" case to return.
    """
    if decision is None:
        return NO_ANSWER_REASON
    return UNSAFE_ANSWER_REASON if unsafe else UNSOLVABLE_REASON


async def log_attempt(account_id: str, channel: str, attempt: int) -> None:
    """One attempt spent: an answer is decided, screened and about to be sent."""
    await log_event(
        "INFO",
        "neurocomment_challenge_attempt",
        account_id=account_id,
        extra={
            "channel": channel,
            "attempts": attempt,
            "reason": f"{attempt}/{settings.neurocomment.challenge_max_attempts}",
        },
    )


async def log_result(account_id: str, channel: str, reason: str) -> None:
    """How the rule ended — passed, or the exact way it did not."""
    await log_event(
        "INFO" if reason in _INFO_REASONS else "WARNING",
        "neurocomment_challenge_result",
        account_id=account_id,
        extra={"channel": channel, "reason": reason},
    )
