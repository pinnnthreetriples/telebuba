"""Operator-started message runs across selected accounts and recipients.

A run is stored (``core.repositories.bulk_runs``) and driven by
:class:`services.accounts._bulk_runner.BulkRun`, so it may span days: it sends only
inside the working window, waits out each account's daily cap instead of dropping
recipients, and resumes after a restart. One run is live at a time. While its loop runs
it holds its accounts in ``services._account_owner`` (``_bulk_claims``), and leaves out
an account another feature is driving.

Every send that may have reached Telegram counts as one first message of the
account's UTC day (``outreach_daily_usage``): the tool cannot tell an answered chat
from a cold one, and its recipients are mostly fresh lookup finds.

A run leaves out, once when it starts, the accounts its protection settings rule
out (resting after a limit, spam-blocked, flooded in the last day): split mode
plans their recipients onto the others, each mode skips their sends.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import UTC, datetime
from typing import Literal, cast, get_args
from uuid import uuid4

from core.channel_tokens import normalize_channel
from core.db import load_warming_settings
from core.gemini import generate_text
from core.openai import generate_text_deepseek
from core.repositories import bulk_runs as repo
from core.repositories.outreach import load_outreach_limits
from core.telegram_client import UNCONFIRMED_ERROR_TYPE, execute
from schemas.bulk_messages import (
    BulkMessageGenerated,
    BulkMessageJob,
    BulkMessageOutcome,
    BulkMessageRequest,
)
from schemas.bulk_runs import BulkRunAccount, BulkRunRecord
from schemas.outreach_protection import OutreachProtection, SkippedAccount, SkipReason
from schemas.run_schedule import RunSchedule
from schemas.telegram_actions import ActionResult, SendChatMessage
from schemas.telegram_actions_outreach import OutreachSetTyping
from services._text_llm import text_llm
from services.accounts._bulk_claims import (
    BUSY_CODE,
    claim_accounts,
    release_accounts,
    send_locked,
)
from services.accounts._bulk_plan import plan_items
from services.accounts._bulk_runner import BulkRun, Lane
from services.accounts._outreach_protection import skipped_accounts, young_account_ids
from services.accounts._result import AccountActionError, raise_for_result

_MAX_RETAINED_JOBS = 20
_OUTCOME_STATUS: dict[str, Literal["ok", "failed", "skipped", "unconfirmed"]] = {
    "ok": "ok",
    "failed": "failed",
    "skipped": "skipped",
    "unconfirmed": "unconfirmed",
}
_FINAL = frozenset(_OUTCOME_STATUS)
_SKIP_REASONS = frozenset(get_args(SkipReason))
# Live runs of this process, so a cancel reaches the loop and shutdown can stop it.
_RUNS: dict[str, BulkRun] = {}
_TASKS: dict[str, asyncio.Task[None]] = {}
logger = logging.getLogger(__name__)


def _recipient_peer(recipient: str) -> str:
    token = recipient.strip()
    if token.isdecimal() and int(token) > 0:
        # Canonical digits, so "0123" and "123" are caught as the same user.
        return str(int(token))
    peer = normalize_channel(token, max_length=32)
    if peer is None or peer.startswith("+"):
        msg = "invalid recipient"
        raise ValueError(msg)
    return peer


async def _resolve(data: BulkMessageRequest) -> tuple[BulkMessageRequest, RunSchedule]:
    """Fill every left-out default from the saved limits, so a resume needs no lookup."""
    limits = await load_outreach_limits()
    schedule = data.schedule or limits.schedule
    delays_missing = data.min_delay_seconds is None or data.max_delay_seconds is None
    resolved = data.model_copy(
        update={
            "min_delay_seconds": (
                limits.bulk_min_delay_seconds if delays_missing else data.min_delay_seconds
            ),
            "max_delay_seconds": (
                limits.bulk_max_delay_seconds if delays_missing else data.max_delay_seconds
            ),
            "daily_cap": data.daily_cap or limits.first_messages_per_day,
            "schedule": schedule,
            "protection": data.protection or limits.protection,
        }
    )
    return resolved, schedule


async def start_bulk_message_job(data: BulkMessageRequest, owner_user_id: str) -> BulkMessageJob:
    """Validate all targets and store the run; one live run prevents duplicates."""
    if await repo.list_live_bulk_runs():
        msg = "bulk_message_run_active"
        raise ValueError(msg)
    recipients = [(recipient, _recipient_peer(recipient)) for recipient in data.recipients]
    if len({peer.casefold() for _, peer in recipients}) != len(recipients):
        msg = "duplicate recipients"
        raise ValueError(msg)
    resolved, schedule = await _resolve(data)
    run_id = uuid4().hex
    now = datetime.now(UTC)
    record = BulkRunRecord(
        id=run_id,
        owner_user_id=owner_user_id,
        status="running",
        request_json=resolved.model_dump_json(),
        created_at=now.isoformat(),
    )
    protection = resolved.protection or OutreachProtection()
    skipped = {s.account_id: s for s in await skipped_accounts(data.account_ids, protection, now)}
    live = [account_id for account_id in resolved.account_ids if account_id not in skipped]
    # Split mode spreads its recipients over the accounts the run keeps; each mode
    # plans every pair, so a left-out account's sends show as skipped.
    split_live = resolved.mode == "split" and live
    planned = resolved.model_copy(update={"account_ids": live}) if split_live else resolved
    items = plan_items(run_id, planned, schedule, recipients)
    for item in items:
        left_out = skipped.get(item.account_id)
        if left_out is not None:
            item.status = "skipped"
            item.error_code = left_out.reason
            item.retry_after_seconds = left_out.retry_after_seconds
            item.finished_at = now.isoformat()
    accounts = [
        BulkRunAccount(
            run_id=run_id,
            account_id=account_id,
            stop_code=skipped[account_id].reason if account_id in skipped else None,
            retry_after_seconds=(
                skipped[account_id].retry_after_seconds if account_id in skipped else None
            ),
        )
        for account_id in resolved.account_ids
    ]
    try:
        await repo.create_bulk_run(record, items, accounts)
    except repo.BulkRunActiveError as exc:
        # Another start won the race the check above cannot close on its own.
        msg = "bulk_message_run_active"
        raise ValueError(msg) from exc
    await repo.prune_bulk_runs(_MAX_RETAINED_JOBS)
    return await _job_view(record)


async def _job_view(record: BulkRunRecord) -> BulkMessageJob:
    items = await repo.list_bulk_run_items(record.id)
    final = sorted(
        (item for item in items if item.status in _FINAL),
        key=lambda item: (item.finished_seq or 0, item.id or 0),
    )
    counted = [item for item in items if not item.handed_over]
    return BulkMessageJob(
        job_id=record.id,
        status=record.status,
        total=len(counted),
        completed=sum(item.status in _FINAL for item in counted),
        results=[
            BulkMessageOutcome(
                account_id=item.account_id,
                recipient=item.recipient,
                status=_OUTCOME_STATUS[item.status],
                error_code=item.error_code,
                retry_after_seconds=item.retry_after_seconds,
                handed_over=item.handed_over,
                time_basis=item.time_basis,
            )
            for item in final
        ],
        wait_reason=record.wait_reason if record.status == "running" else None,
        next_send_at=record.next_send_at if record.status == "running" else None,
        skipped_accounts=[
            SkippedAccount(
                account_id=row.account_id,
                reason=cast("SkipReason", row.stop_code),
                retry_after_seconds=row.retry_after_seconds,
            )
            for row in await repo.list_bulk_run_accounts(record.id)
            if row.stop_code in _SKIP_REASONS
        ],
    )


async def get_bulk_message_job(job_id: str, owner_user_id: str) -> BulkMessageJob | None:
    record = await repo.get_bulk_run(job_id)
    if record is None or record.owner_user_id != owner_user_id:
        return None
    return await _job_view(record)


async def get_active_bulk_message_job(owner_user_id: str) -> BulkMessageJob | None:
    record = await repo.latest_bulk_run(owner_user_id, live_only=True)
    return None if record is None else await _job_view(record)


async def get_latest_bulk_message_job(owner_user_id: str) -> BulkMessageJob | None:
    record = await repo.latest_bulk_run(owner_user_id, live_only=False)
    return None if record is None else await _job_view(record)


async def cancel_bulk_message_job(job_id: str, owner_user_id: str) -> BulkMessageJob | None:
    """Stop after the sends in flight; any wait (pause, hours, next day) ends at once."""
    record = await repo.get_bulk_run(job_id)
    if record is None or record.owner_user_id != owner_user_id:
        return None
    if record.status == "running":
        await repo.update_bulk_run(job_id, cancel_requested=1)
        run = _RUNS.get(job_id)
        if run is not None:
            run.cancel.set()
            run.wakeup.set()
    return await _job_view(record)


def _failure_code(result: ActionResult) -> str | None:
    if result.status == "ok":
        return None
    if result.error_type == "AccountNotFound":
        return "account_not_found"
    try:
        raise_for_result(result)
    except AccountActionError as exc:
        return exc.code
    return "failed"


async def _send_one(account_id: str, recipient: str, peer: str, text: str) -> BulkMessageOutcome:
    try:
        result = await execute(
            account_id,
            SendChatMessage(recipient=peer, text=text),
            domain="bulk_messages",
        )
        status = "unconfirmed" if result.error_type == UNCONFIRMED_ERROR_TYPE else None
        error_code = "delivery_unconfirmed" if status else _failure_code(result)
        retry_after_seconds = result.flood_wait_seconds
    except Exception as exc:  # noqa: BLE001 - one pair must not stop the batch
        logger.warning("bulk message pair failed: %s", type(exc).__name__)
        # execute may raise after the Telegram write (for example while
        # logging its result), so a resend is not known to be safe.
        status = "unconfirmed"
        error_code = "delivery_unconfirmed"
        retry_after_seconds = None
    return BulkMessageOutcome(
        account_id=account_id,
        recipient=recipient,
        status=status or ("failed" if error_code else "ok"),
        error_code=error_code,
        retry_after_seconds=retry_after_seconds,
    )


async def _send(account_id: str, recipient: str, peer: str, text: str) -> BulkMessageOutcome:
    # Under the lifecycle lock, so a channel search that pulled the account pauses
    # this send instead of sharing the session. ``_send_one`` is looked up at call
    # time, so tests can patch ``execute`` on this module.
    return await send_locked(account_id, lambda: _send_one(account_id, recipient, peer, text))


async def _show_typing(account_id: str, peer: str, min_seconds: float, max_seconds: float) -> None:
    """Show "typing…" to the recipient for a random moment; a failure only skips the show.

    Never raises: the send that follows must go out exactly as it would without it.
    """
    try:
        result = await execute(
            account_id, OutreachSetTyping(recipient=peer), domain="bulk_messages"
        )
    except Exception as exc:  # noqa: BLE001 - typing must never stop or repeat the send
        logger.warning("bulk message typing failed: %s", type(exc).__name__)
        return
    if result.status == "ok":
        await asyncio.sleep(random.uniform(min_seconds, max_seconds))  # noqa: S311  # nosec B311 - timing jitter, not a secret


async def _load_run(record: BulkRunRecord) -> BulkRun:
    data = BulkMessageRequest.model_validate_json(record.request_json)
    # Stored requests are resolved, so the fallback only guards a hand-edited row.
    schedule = data.schedule or RunSchedule()
    protection = data.protection or OutreachProtection()
    young = await young_account_ids(data.account_ids, protection, datetime.now(UTC))
    accounts = {a.account_id: a for a in await repo.list_bulk_run_accounts(record.id)}
    cancel = asyncio.Event()
    if record.cancel_requested:
        cancel.set()
    return BulkRun(
        run_id=record.id,
        data=data,
        schedule=schedule,
        items=await repo.list_bulk_run_items(record.id),
        lanes={
            account_id: Lane(
                row=accounts.get(account_id)
                or BulkRunAccount(run_id=record.id, account_id=account_id),
                young=account_id in young,
            )
            for account_id in data.account_ids
        },
        send=_send,
        show_typing=_show_typing,
        protection=protection,
        cancel=cancel,
        start_at=data.start_at,
        seq=await repo.max_finished_seq(record.id),
    )


async def run_bulk_message_job(job_id: str) -> None:
    """Drive a stored run to its end; the row's final status says how it ended."""
    record = await repo.get_bulk_run(job_id)
    if record is None or record.status != "running":
        return
    if job_id in _RUNS:
        # Already driven by this process: a second loop would send every item twice,
        # and its end would release the claims the first one still sends under.
        return
    run = await _load_run(record)
    if job_id in _RUNS:
        # A concurrent call got past the check above while this one loaded the run.
        return
    _RUNS[job_id] = run
    try:
        live = [account_id for account_id, lane in run.lanes.items() if lane.row.stop_code is None]
        await run.leave_out(await claim_accounts(job_id, live), BUSY_CODE)
        cancelled = await run.run()
    except Exception as exc:  # noqa: BLE001 - a broken run must not stay live forever
        logger.warning("bulk message run failed: %s", type(exc).__name__)
        # A send whose result was never stored may have gone out: never resend it.
        await repo.unconfirm_sending_items(job_id)
        cancelled = True
    finally:
        # Every end lands here — done, failed, cancelled by the operator or by
        # shutdown — so no claim outlives the loop that sends under it.
        _RUNS.pop(job_id, None)
        release_accounts(job_id)
    await repo.update_bulk_run(
        job_id,
        status="cancelled" if cancelled else "completed",
        finished_at=datetime.now(UTC).isoformat(),
        next_send_at=None,
        wait_reason=None,
    )


def launch_bulk_message_job(job_id: str) -> None:
    """Run in the background, tracked so shutdown can stop it and a restart resume it."""
    task = asyncio.create_task(run_bulk_message_job(job_id))
    _TASKS[job_id] = task
    task.add_done_callback(lambda _: _TASKS.pop(job_id, None))


async def reconcile_bulk_messages_on_startup() -> None:
    """Resume every live run; a send a restart cut short is marked unconfirmed, never redone."""
    for record in await repo.list_live_bulk_runs():
        await repo.unconfirm_sending_items(record.id)
        launch_bulk_message_job(record.id)


async def shutdown_bulk_messages_on_shutdown() -> None:
    """Stop the loops but leave the runs live, so the next start resumes them."""
    tasks = list(_TASKS.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def generate_bulk_message(prompt: str) -> BulkMessageGenerated:
    """Use the configured text provider; generated text remains an editable draft."""
    llm = text_llm(await load_warming_settings())
    if not llm.api_key:
        msg = "generator_unavailable"
        raise ValueError(msg)
    generate = generate_text_deepseek if llm.use_deepseek else generate_text
    result = await generate(
        llm.request(
            "Write one Telegram message based on this instruction. "
            "Return only the message text, without a preface or quotes.\n\n"
            f"{prompt}"
        )
    )
    if result.status != "ok" or not result.text or not result.text.strip():
        msg = "generation_failed"
        raise ValueError(msg)
    return BulkMessageGenerated(
        text=result.text.strip()[:4096],
        provider="deepseek" if llm.use_deepseek else "gemini",
    )
