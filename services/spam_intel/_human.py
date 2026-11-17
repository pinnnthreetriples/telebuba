"""The probe as a person, on the services side (docs/spam-intel.md 5.7): the plan and the moment.

Two switches live here, both off by default:

* ``human_sequence`` — :func:`draw_plan` draws the pauses of one conversation (``core`` only plays
  them); the gateway hands the plan to the executor, which asks through
  ``core.telegram_client.converse_spam_status`` instead of the single ``/start`` probe.
* ``live_session_embedding`` — :func:`live_moment` decides WHEN an ask may probe, from whether
  the account's pooled client is already connected: a ROUTINE or DECISION ask for a live account
  waits a jittered moment into that session; a ROUTINE ask for an idle one is deferred rather
  than connecting it just to probe. DECISION, MEASUREMENT and the operator's button still connect.

Nothing here reads the database, the budget or the ledger: the gateway keeps its accounting.
"""

from __future__ import annotations

import asyncio
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from core.telegram_client import has_live_client
from schemas.spam_probe import HumanProbePlan

if TYPE_CHECKING:
    from core._config_spam_intel import SpamIntelSettings
    from schemas.spam_intel import Purpose

# The purposes that ride a session already open; the others may open one.
_SESSION_PURPOSES = frozenset({"routine", "decision"})


class _Uniform(Protocol):
    def uniform(self, a: float, b: float, /) -> float: ...


class Moment(StrEnum):
    """What the gateway does about the moment of a probe."""

    NOW = "now"  # no live-session rule applies: probe at the caller's moment
    WAIT = "wait"  # the account is live: probe after a jittered pause
    DEFER = "defer"  # a routine ask for an idle account: do not connect it just to ask


def draw_plan(rng: _Uniform, cfg: SpamIntelSettings) -> HumanProbePlan:
    """The pauses of one conversation, each drawn from its configured range."""
    return HumanProbePlan(
        pre_open=rng.uniform(cfg.human_pre_open_min_seconds, cfg.human_pre_open_max_seconds),
        open_to_typing=rng.uniform(
            cfg.human_open_to_typing_min_seconds, cfg.human_open_to_typing_max_seconds
        ),
        typing_seconds=rng.uniform(cfg.human_typing_min_seconds, cfg.human_typing_max_seconds),
        reply_wait_max=cfg.human_reply_wait_seconds,
        quiet_after_last_reply=rng.uniform(
            cfg.human_quiet_min_seconds, cfg.human_quiet_max_seconds
        ),
        read_delay=rng.uniform(cfg.human_read_delay_min_seconds, cfg.human_read_delay_max_seconds),
    )


def is_live(account_id: str) -> bool:
    """Whether the account's client is already connected (a seam: tests patch it)."""
    return has_live_client(account_id)


def live_moment(account_id: str, purpose: Purpose, cfg: SpamIntelSettings) -> Moment:
    """The moment rule for one ask; always ``NOW`` while ``live_session_embedding`` is off."""
    if not cfg.live_session_embedding or purpose not in _SESSION_PURPOSES:
        return Moment.NOW
    if is_live(account_id):
        return Moment.WAIT
    return Moment.DEFER if purpose == "routine" else Moment.NOW


def live_delay(rng: _Uniform, cfg: SpamIntelSettings) -> float:
    """The jittered pause before a probe in a live session, in seconds."""
    return rng.uniform(cfg.live_session_delay_min_seconds, cfg.live_session_delay_max_seconds)


async def sleep(seconds: float) -> None:
    """The moment's sleep seam: tests replace it so a wait runs instantly."""
    if seconds > 0:
        await asyncio.sleep(seconds)
