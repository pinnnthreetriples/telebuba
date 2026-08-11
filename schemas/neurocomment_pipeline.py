"""Typed durable states for the Neurocomment post pipeline."""

from __future__ import annotations

from enum import StrEnum


class PipelineOutcome(StrEnum):
    """What the durable inbox may safely do after one pipeline attempt."""

    TERMINAL = "terminal"
    RETRYABLE = "retryable"
    AMBIGUOUS = "ambiguous"


class InboxStage(StrEnum):
    """Last durable boundary crossed by an inbox worker."""

    RECEIVED = "received"
    PRE_SEND = "pre_send"
    DISPATCHING = "dispatching"
    DISPATCHED = "dispatched"
