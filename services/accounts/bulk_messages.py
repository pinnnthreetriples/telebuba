"""Operator-started message runs across selected accounts and recipients."""

from __future__ import annotations

import asyncio
import logging
import random
from contextlib import suppress
from uuid import uuid4

from core.channel_tokens import normalize_channel
from core.config import settings
from core.db import load_warming_settings
from core.gemini import generate_text
from core.openai import generate_text_deepseek
from core.telegram_client import UNCONFIRMED_ERROR_TYPE, execute
from schemas.bulk_messages import (
    BulkMessageGenerated,
    BulkMessageJob,
    BulkMessageOutcome,
    BulkMessageRequest,
)
from schemas.gemini import GeminiRequest
from schemas.telegram_actions import ActionResult, SendChatMessage
from services.accounts._result import AccountActionError, raise_for_result

_jobs: dict[str, BulkMessageJob] = {}
_job_owners: dict[str, str] = {}
_pending: dict[str, tuple[BulkMessageRequest, list[tuple[str, str]]]] = {}
_cancel_events: dict[str, asyncio.Event] = {}
_MAX_RETAINED_JOBS = 20
_ACCOUNT_LIMIT_STATUSES = frozenset({"flood_wait", "slow_mode_wait", "premium_wait", "peer_flood"})
# Codes where the account provably sent nothing and cannot send for the rest of the
# run: stop using it (split hands the recipient on). "unconfirmed" never lands here.
_STOP_CODES = _ACCOUNT_LIMIT_STATUSES | {
    "account_not_found",
    "session_dead",
    "account_deactivated",
    "account_frozen",
    "unavailable",
}
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


def start_bulk_message_job(data: BulkMessageRequest, owner_user_id: str) -> BulkMessageJob:
    """Validate all targets before accepting a run; one active run prevents duplicates."""
    if any(job.status == "running" for job in _jobs.values()):
        msg = "bulk_message_run_active"
        raise ValueError(msg)
    recipients = [(recipient, _recipient_peer(recipient)) for recipient in data.recipients]
    if len({peer.casefold() for _, peer in recipients}) != len(recipients):
        msg = "duplicate recipients"
        raise ValueError(msg)
    for old_id, old_job in list(_jobs.items()):
        if len(_jobs) < _MAX_RETAINED_JOBS:
            break
        if old_job.status != "running":
            del _jobs[old_id]
            del _job_owners[old_id]
    job = BulkMessageJob(
        job_id=uuid4().hex,
        status="running",
        total=len(recipients) * (1 if data.mode == "split" else len(data.account_ids)),
        completed=0,
        results=[],
    )
    _jobs[job.job_id] = job
    _job_owners[job.job_id] = owner_user_id
    _pending[job.job_id] = (data, recipients)
    _cancel_events[job.job_id] = asyncio.Event()
    return job.model_copy(deep=True)


def get_bulk_message_job(job_id: str, owner_user_id: str) -> BulkMessageJob | None:
    if _job_owners.get(job_id) != owner_user_id:
        return None
    job = _jobs.get(job_id)
    return job.model_copy(deep=True) if job is not None else None


def get_active_bulk_message_job(owner_user_id: str) -> BulkMessageJob | None:
    for job_id, job in _jobs.items():
        if _job_owners[job_id] == owner_user_id and job.status == "running":
            return job.model_copy(deep=True)
    return None


def get_latest_bulk_message_job(owner_user_id: str) -> BulkMessageJob | None:
    for job_id in reversed(_jobs):
        if _job_owners[job_id] == owner_user_id:
            return _jobs[job_id].model_copy(deep=True)
    return None


def cancel_bulk_message_job(job_id: str, owner_user_id: str) -> BulkMessageJob | None:
    """Stop after the current Telegram call; a pacing wait is interrupted at once."""
    if _job_owners.get(job_id) != owner_user_id:
        return None
    job = _jobs.get(job_id)
    if job is None:
        return None
    event = _cancel_events.get(job_id)
    if event is not None:
        event.set()
    return job.model_copy(deep=True)


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


async def _pace(data: BulkMessageRequest, cancel_event: asyncio.Event) -> bool:
    """Wait the jittered delay; False once the run is cancelled."""
    delay = random.uniform(  # noqa: S311  # nosec B311 - timing jitter, not a secret
        data.min_delay_seconds, data.max_delay_seconds
    )
    if delay:
        with suppress(TimeoutError):
            await asyncio.wait_for(cancel_event.wait(), timeout=delay)
    return not cancel_event.is_set()


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


def _skipped(account_id: str, recipient: str, stop: BulkMessageOutcome) -> BulkMessageOutcome:
    return BulkMessageOutcome(
        account_id=account_id,
        recipient=recipient,
        status="skipped",
        error_code=stop.error_code,
        retry_after_seconds=stop.retry_after_seconds,
    )


async def _run_account_messages(
    account_id: str,
    data: BulkMessageRequest,
    recipients: list[tuple[str, str]],
    job: BulkMessageJob,
    cancel_event: asyncio.Event,
) -> None:
    blocked: BulkMessageOutcome | None = None
    for recipient, peer in recipients:
        if cancel_event.is_set():
            break
        if blocked is not None:
            job.results.append(_skipped(account_id, recipient, blocked))
            job.completed += 1
            continue
        if job.completed and not await _pace(data, cancel_event):
            break
        outcome = await _send_one(account_id, recipient, peer, data.text)
        if outcome.error_code in _STOP_CODES:
            blocked = outcome
        job.results.append(outcome)
        job.completed += 1


async def _run_split_messages(
    data: BulkMessageRequest,
    recipients: list[tuple[str, str]],
    job: BulkMessageJob,
    cancel_event: asyncio.Event,
) -> None:
    """One message per recipient, round-robin; a stopped account hands its turn on."""
    live = list(data.account_ids)
    stops: dict[str, BulkMessageOutcome] = {}
    cursor = 0
    attempted = False
    for index, (recipient, peer) in enumerate(recipients):
        pinned = data.recipient_accounts.get(recipient)
        last: BulkMessageOutcome | None = None
        refused: list[BulkMessageOutcome] = []
        while live and (pinned is None or pinned in live):
            if cancel_event.is_set() or (attempted and not await _pace(data, cancel_event)):
                # A refusal awaiting hand-over was never handed on: it is final.
                job.results.extend(refused)
                job.completed += len(refused)
                return
            job.results.extend(r.model_copy(update={"handed_over": True}) for r in refused)
            refused = []
            account_id = pinned or live[cursor % len(live)]
            attempted = True
            last = await _send_one(account_id, recipient, peer, data.text)
            if last.error_code not in _STOP_CODES:
                if pinned is None:
                    cursor += 1
                break
            stops[account_id] = last
            live.remove(account_id)
            if pinned is not None or not live:
                break
            refused = [last]
            last = None
        if last is None:
            planned = pinned or data.account_ids[index % len(data.account_ids)]
            stop = stops.get(planned) or list(stops.values())[-1]
            last = _skipped(planned, recipient, stop)
        job.results.append(last)
        job.completed += 1


async def run_bulk_message_job(job_id: str) -> None:
    """Send sequentially; each pair gets its own outcome even after a refusal."""
    data, recipients = _pending[job_id]
    job = _jobs[job_id]
    cancel_event = _cancel_events[job_id]
    try:
        if data.mode == "split":
            await _run_split_messages(data, recipients, job, cancel_event)
        else:
            for account_id in data.account_ids:
                if cancel_event.is_set():
                    break
                await _run_account_messages(account_id, data, recipients, job, cancel_event)
    finally:
        job.status = (
            "cancelled" if cancel_event.is_set() and job.completed < job.total else "completed"
        )
        _pending.pop(job_id, None)
        _cancel_events.pop(job_id, None)


async def generate_bulk_message(prompt: str) -> BulkMessageGenerated:
    """Use the configured text provider; generated text remains an editable draft."""
    use_deepseek = bool(settings.deepseek.api_key)
    if use_deepseek:
        api_key = settings.deepseek.api_key
        model = settings.deepseek.model
        llm = settings.deepseek
        generate = generate_text_deepseek
    else:
        secret = await load_warming_settings()
        api_key = secret.gemini_api_key
        model = secret.gemini_model
        llm = settings.gemini
        generate = generate_text
    if not api_key:
        msg = "generator_unavailable"
        raise ValueError(msg)
    result = await generate(
        GeminiRequest(
            api_key=api_key,
            model=model,
            prompt=(
                "Write one Telegram message based on this instruction. "
                "Return only the message text, without a preface or quotes.\n\n"
                f"{prompt}"
            ),
            temperature=llm.temperature,
            max_output_tokens=llm.max_output_tokens,
        )
    )
    if result.status != "ok" or not result.text or not result.text.strip():
        msg = "generation_failed"
        raise ValueError(msg)
    return BulkMessageGenerated(
        text=result.text.strip()[:4096],
        provider="deepseek" if use_deepseek else "gemini",
    )
