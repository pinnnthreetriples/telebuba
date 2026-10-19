"""Where each phone stint's preparation stands, for the card and the visit gate.

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


def phone_prep(account_id: str) -> PhonePrep | None:
    """The card's view of the account's phone job: preparing, failed, or ``None``."""
    return STATE.get(account_id)
