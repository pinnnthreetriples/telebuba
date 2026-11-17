"""Contracts of the human-like @SpamBot probe (docs/spam-intel.md section 5.7).

No behaviour, no I/O. ``services.spam_intel`` draws a :class:`HumanProbePlan` (the pauses of one
conversation), ``core.telegram_client`` plays it against the account's client and hands back a
:class:`SpambotTranscript` of everything the bot said.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class HumanProbePlan(BaseModel):
    """The pauses of one conversation with @SpamBot, in seconds, drawn by ``services``.

    ``pre_open`` before the dialog is opened, ``open_to_typing`` between opening it and the
    "typing" action, ``typing_seconds`` between that action and ``/start``. ``reply_wait_max`` is
    how long to wait for the FIRST message of the bot; ``quiet_after_last_reply`` is how long
    without a further message ends the collection; ``read_delay`` is the pause before the dialog
    is marked read.
    """

    model_config = ConfigDict(frozen=True)

    pre_open: float = Field(ge=0.0)
    open_to_typing: float = Field(ge=0.0)
    typing_seconds: float = Field(ge=0.0)
    reply_wait_max: float = Field(gt=0.0)
    quiet_after_last_reply: float = Field(gt=0.0)
    read_delay: float = Field(ge=0.0)


class SpambotTranscript(BaseModel):
    """What @SpamBot said back to ``/start``: every message of it, in arrival order.

    ``texts`` holds the messages that carry text; ``n_messages`` counts all of them (a message
    with no text still counts); ``has_markup`` says whether any carried a keyboard or inline
    buttons (nothing is ever pressed); ``elapsed`` is the whole conversation in seconds.
    """

    texts: list[str] = Field(default_factory=list)
    has_markup: bool = False
    n_messages: int = Field(default=0, ge=0)
    elapsed: float = Field(default=0.0, ge=0.0)
