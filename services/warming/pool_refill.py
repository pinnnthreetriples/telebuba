"""The automatic refill: a language the pool runs short of is searched on its own.

"Short" is the board's own warning (``language_shortages``): fewer channels of a
language than its accounts read from (the affinity floor). Every
``pool_refill_check_minutes`` one pass looks at the shortest language and runs the same
pool search as the «Найти каналы» button, on the interests of that language's accounts.

**It never loops.** One language per pass, and one language is searched at most once
per ``pool_refill_interval_hours`` — any search counts, the button's too, whatever it
found. A pass during another pool search does nothing.

**It never takes a busy account.** With no free account the pass leaves a request
instead (a ``no_account`` row, written once until something changes), and the board
shows it as «Нужен аккаунт для поиска каналов»: the operator opens the same consent
dialog the button uses. Asking again costs no Telegram read, so the next pass simply
tries again and the first free account clears the request.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime

from core.config import settings
from core.db import list_accounts, list_warming_channels, list_warming_states
from core.logging import log_event
from core.phone_geo import country_for_phone
from core.repositories.warming_searches import list_channel_searches_since, record_channel_search
from schemas._warming_pool import ChannelSearchRecord, PoolFillRequest
from schemas.warming import is_warming
from services._account_language import account_language
from services.warming import pool_fill
from services.warming._channel_language import language_shortages
from services.warming._pool_requests import latest_pool_search, refill_interval

# Stdlib sink for the traceback; the persisted event carries only the error type.
logger = logging.getLogger(__name__)

_SECONDS_PER_MINUTE = 60
_NEEDS_ACCOUNT = frozenset({"needs_consent", "no_search_account"})


@dataclass
class _State:
    task: asyncio.Task[None] | None = None


_state = _State()


async def _phones() -> list[str | None]:
    """The phones of the accounts that read the pool: warming, or ready to warm."""
    states = {record.account_id: record for record in await list_warming_states()}
    return [
        account.phone
        for account in (await list_accounts()).accounts
        if (state := states.get(account.account_id)) is None
        or is_warming(state.state)
        or not state.promoted_to_nc
    ]


def _country(language: str, phones: list[str | None]) -> str | None:
    """The most common phone country among the accounts of ``language``."""
    countries = Counter(
        country
        for phone in phones
        if account_language(phone) == language and (country := country_for_phone(phone))
    )
    return countries.most_common(1)[0][0] if countries else None


async def run_refill_pass(now: datetime | None = None) -> str | None:
    """Search the shortest language when it is due; the language acted on, or ``None``."""
    if not settings.warming.pool_refill_enabled or pool_fill.is_running():
        return None
    moment = now or datetime.now(UTC)
    phones = await _phones()
    shortages = language_shortages(phones, (await list_warming_channels()).channels)
    rows = await list_channel_searches_since(moment - refill_interval())
    for shortage in shortages:
        latest = latest_pool_search(rows, shortage.language)
        if latest is not None and latest.status != "no_account":
            continue
        request = PoolFillRequest(
            language=shortage.language, country=_country(shortage.language, phones)
        )
        result = await pool_fill.find_channels(request, origin="refill")
        if result.status in _NEEDS_ACCOUNT and latest is None:
            await _ask_for_account(shortage.language, moment)
        if result.status == "no_topics":
            continue
        return shortage.language
    return None


async def _ask_for_account(language: str, moment: datetime) -> None:
    await record_channel_search(
        ChannelSearchRecord(
            origin="refill",
            language=language,
            status="no_account",
            created_at=moment.isoformat(),
        )
    )
    await log_event("WARNING", "warming_pool_refill_needs_account", extra={"language": language})


async def _loop() -> None:
    await asyncio.sleep(settings.warming.pool_refill_startup_delay_seconds)
    while True:
        try:
            await run_refill_pass()
        except Exception as exc:  # noqa: BLE001 - one failed pass must not end the loop.
            logger.warning("pool refill pass failed", exc_info=exc)
            await log_event(
                "WARNING",
                "warming_pool_refill_failed",
                extra={"error_type": type(exc).__name__},
            )
        await asyncio.sleep(settings.warming.pool_refill_check_minutes * _SECONDS_PER_MINUTE)


async def start_pool_refill() -> None:
    """Start the refill task (lifespan startup); a no-op when switched off or running."""
    if not settings.warming.pool_refill_enabled or _state.task is not None:
        return
    _state.task = asyncio.create_task(_loop(), name="warming-pool-refill")


async def shutdown_pool_refill() -> None:
    """Cancel the refill task and wait for it to unwind."""
    task, _state.task = _state.task, None
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def reset_for_tests() -> None:
    """Forget the task between tests."""
    _state.task = None
