"""The start dialog's two entry points: a start with geo consent, and «Вернуть в прогрев».

Geo never refuses a start: a phone/proxy country mismatch is a warning the operator
can accept. The acceptance is stored after the start went through, as the pair it
covers (``_console.geo_pair``), so a later proxy in another country asks again.

A resume is a start that keeps the graduated account's stint — its clock and worked
days — under the same per-account lock, clearing the graduation flags on the way
(``start_warming`` does both; ``carry_or_restamp`` decides what carries).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.db import fetch_account
from core.logging import log_event
from core.repositories.warming_marks import store_geo_ack
from services.warming import _runtime
from services.warming._console import account_geo, geo_pair
from services.warming._state import _current_card

if TYPE_CHECKING:
    from schemas.warming import StartWarmingRequest, WarmingAccountState


async def _record_geo_consent(data: StartWarmingRequest) -> bool:
    """Store the accepted country pair; ``False`` when there was nothing to accept."""
    if not data.accept_geo_mismatch:
        return False
    account = await fetch_account(data.account_id)
    pair = geo_pair(account_geo(account)) if account is not None else None
    if pair is None:
        return False
    await store_geo_ack(data.account_id, pair)
    await log_event(
        "INFO",
        "warming_geo_mismatch_accepted",
        account_id=data.account_id,
        extra={"pair": pair},
    )
    return True


async def _start(data: StartWarmingRequest, *, resume: bool) -> WarmingAccountState:
    card = await _runtime.start_warming(data, resume=resume)
    if await _record_geo_consent(data):
        return await _current_card(data.account_id)
    return card


async def start_warming_with_consent(data: StartWarmingRequest) -> WarmingAccountState:
    """Start warming and remember a geo consent given in the start dialog."""
    return await _start(data, resume=False)


async def resume_warming(data: StartWarmingRequest) -> WarmingAccountState:
    """«Вернуть в прогрев»: un-graduate and continue the stint where it stopped."""
    card = await _start(data, resume=True)
    await log_event("INFO", "warming_resumed", account_id=data.account_id)
    return card
