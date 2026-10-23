"""Where each phone stint's preparation stands, for the card and the visit gate.

Also which phones found no channel to read on their last visit, for the card's note.

A leaf of the warming package (``board`` reads it, ``_mobile_prep`` writes it), so
the card does not import the job's pod machinery. Process-local, like the jobs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncio

    from schemas.warming import PhonePrep

STATE: dict[str, PhonePrep] = {}
# Strong references to the running jobs; the event loop holds tasks weakly.
JOBS: dict[str, asyncio.Task[None]] = {}
# Phones whose last visit found no channel row on the chat list (``_mobile_cycle``).
NO_CHANNELS: set[str] = set()


def phone_prep(account_id: str) -> PhonePrep | None:
    """The card's view of the account's phone job: preparing, failed, or ``None``."""
    return STATE.get(account_id)


def phone_no_channels(account_id: str) -> bool:
    """Whether the account's phone found no channel to read on its last visit."""
    return account_id in NO_CHANNELS
