"""Typed durable states for the Neurocomment post pipeline."""

from __future__ import annotations

from enum import StrEnum


class PipelineOutcome(StrEnum):
    """What the durable inbox may safely do after one pipeline attempt."""

    TERMINAL = "terminal"
    RETRYABLE = "retryable"
    AMBIGUOUS = "ambiguous"


class ReleaseOutcome(StrEnum):
    """What the durable inbox did with one failed attempt.

    ``release_post`` used to answer this with a bare ``True``/``False``, which folded
    three very different endings into one falsy value: a spent retry budget, a row that
    crossed the dispatch boundary and can only be settled ambiguous, and a row no longer
    claimed by this worker. Only the first two are worth telling the operator about, and
    neither could be told apart from the third.
    """

    REQUEUED = "requeued"
    EXHAUSTED = "retry_exhausted"
    AMBIGUOUS = "ambiguous"
    UNCLAIMED = "unclaimed"


class InboxStage(StrEnum):
    """Last durable boundary crossed by an inbox worker."""

    RECEIVED = "received"
    PRE_SEND = "pre_send"
    DISPATCHING = "dispatching"
    DISPATCHED = "dispatched"
