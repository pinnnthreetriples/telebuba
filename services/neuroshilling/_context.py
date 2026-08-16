"""What one pass of a campaign carries with it, read once when the run starts.

Its own module because both halves of the step pipeline need it — ``_steps`` picks the
speaker and reserves the row, ``_dispatch`` publishes it and records the outcome — and
either of them owning the type would make the other import its sibling backwards.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from schemas.neuroshilling import NeuroshillingCampaign
    from schemas.neuroshilling_scenario import NeuroshillingStep


class RunContext(NamedTuple):
    """The campaign, its dialogue and its cast, plus the run's own halt set.

    ``halted`` is mutable and deliberately so: it is consulted on every step of every
    target, and re-reading the presence table for that would be a query per step. Every
    verdict that puts an account in it is written down at the same moment by
    ``_telegram.record_send_verdict`` — a flood as ``flooded``, a dead session or a ban
    as ``retired`` — so a restart does not lose what this set is a cache of.
    """

    campaign: NeuroshillingCampaign
    run_id: str
    steps: list[NeuroshillingStep]
    # position -> step, for the reply-anchor walk and the reaction's target.
    by_position: dict[int, NeuroshillingStep]
    # role_id -> the accounts that may speak that part.
    by_role: dict[str, list[str]]
    halted: set[str]
