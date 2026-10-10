"""Start a parser run, follow it, stop it — and the process-lifetime hooks around runs.

A run is background work: the start validates, reserves the accounts and spawns, and the
modal polls :func:`get_run` (nudged by the ``user_parser_progress`` SSE signal) until the
run settles. Refusals are statuses, not exceptions.
"""

from __future__ import annotations

import contextlib
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from core.config import settings
from core.logging import log_event
from core.repositories import user_parser as repository
from schemas.user_parser import UserParserStartOutcome
from schemas.user_parser_run import UserParserAccountProgress, UserParserRun
from services import _account_owner
from services.user_parser import _run, _seams, _state
from services.user_parser._collect import Collector
from services.user_parser._context import RunContext, source_state
from services.user_parser._pool import OWNER, check_accounts, claim_accounts, release_accounts

if TYPE_CHECKING:
    from schemas.user_parser import UserParserRequest

# Locale-neutral 422 codes a start is refused with when it asks for more than the fleet
# ceilings allow; the SPA owns the wording.
TOO_MANY_SOURCES = "user_parser_too_many_sources"
TOO_MANY_ACCOUNTS = "user_parser_too_many_accounts"


class UserParserInvalidError(ValueError):
    """The request is well-formed but past a configured ceiling."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _default_name(request: UserParserRequest, created_at: str) -> str:
    # Locale-neutral on purpose: the modal sends its own translated name; this is only
    # what a client that sent none gets, and the operator can rename it.
    return f"{request.mode} · {created_at[:10]}"


def _context(run_id: str, request: UserParserRequest, names: dict[str, str]) -> RunContext:
    now = _seams.now()
    created_at = now.isoformat()
    sources = [
        source_state(raw, messages_mode=request.mode == "messages") for raw in request.sources
    ]
    live = UserParserRun(
        run_id=run_id,
        name=(request.name or "").strip() or _default_name(request, created_at),
        mode=request.mode,
        status="running",
        created_at=created_at,
        sources_total=len(sources),
        accounts=[
            UserParserAccountProgress(account_id=account_id, name=name)
            for account_id, name in names.items()
        ],
    )
    live.sources = [source.report for source in sources]
    min_date = None
    if request.mode == "messages":
        min_date = now - timedelta(days=request.limits.days)
    return RunContext(
        run_id=run_id,
        request=request,
        sources=sources,
        collector=Collector([source.raw for source in sources]),
        live=live,
        min_date=min_date,
    )


def _check_ceilings(request: UserParserRequest) -> None:
    if len(request.sources) > settings.user_parser.max_sources_per_run:
        raise UserParserInvalidError(TOO_MANY_SOURCES)
    if len(request.account_ids) > settings.user_parser.max_accounts_per_run:
        raise UserParserInvalidError(TOO_MANY_ACCOUNTS)


async def start_run(request: UserParserRequest) -> UserParserStartOutcome:
    """Reserve every picked account, open the run's row and spawn it — or refuse, naming one."""
    _check_ceilings(request)
    # Sorted, for the locks below: two starts picking overlapping accounts must take
    # them in one order, or they deadlock each other.
    account_ids = sorted(request.account_ids)
    names = await check_accounts(account_ids)
    if isinstance(names, UserParserStartOutcome):
        return names
    run_id = uuid4().hex
    from services.warming import account_lock  # noqa: PLC0415 - avoid a load-time cycle

    async with contextlib.AsyncExitStack() as locks:
        for account_id in account_ids:
            await locks.enter_async_context(account_lock(account_id))
        taken = await claim_accounts(run_id, account_ids)
        if taken is not None:
            status = (
                "already_running" if _account_owner.owner_of(taken) == OWNER else "account_busy"
            )
            return UserParserStartOutcome(status=status, refused_account_id=taken)
        try:
            ctx = _context(run_id, request, {a: names[a] for a in request.account_ids})
            await repository.create_run(ctx.live, request)
            _state.spawn(ctx.live, _run.run(ctx, account_ids))
        except BaseException:
            release_accounts(run_id, account_ids)
            raise
    await log_event(
        "INFO",
        "user_parser_run_started",
        extra={
            "run_id": run_id,
            "mode": request.mode,
            "sources": len(request.sources),
            "account_ids": account_ids,
        },
    )
    return UserParserStartOutcome(status="started", run_id=run_id)


async def get_run(run_id: str) -> UserParserRun | None:
    """The live run while it collects, else its stored row; ``None`` for an unknown id."""
    entry = _state.live(run_id)
    if entry is not None:
        return entry.run.model_copy(deep=True)
    return await repository.fetch_run(run_id)


async def stop_run(run_id: str) -> UserParserRun | None:
    """Stop a run; what it collected is saved. Stopping a settled run is a no-op."""
    if _state.request_stop(run_id):
        await _state.wait_settled(run_id)
    return await repository.fetch_run(run_id)


async def reconcile_user_parser_on_startup() -> None:
    """A run the previous process left ``running`` can never finish: mark it interrupted."""
    _account_owner.release_owner(OWNER)
    count = await repository.interrupt_running(_seams.now().isoformat())
    if count:
        await log_event("WARNING", "user_parser_runs_interrupted", extra={"runs": count})


async def shutdown_user_parser_on_shutdown() -> None:
    """Cancel every run (each saves what it has as ``interrupted``) before the pool goes."""
    await _state.shutdown()
