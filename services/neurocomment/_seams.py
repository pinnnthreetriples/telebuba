"""Injectable seams, centralised so tests patch them in one place.

The neurocomment domain reaches Telegram (``execute`` / ``execute_read`` /
``download_post_image``), the LLMs (``generate_text`` for Gemini,
``generate_text_deepseek`` for the text generator, ``generate_text_openai`` for the
alternative solver), Telemetr.io (``search_telemetr``),
the spam probe (``refresh_spam_status``), randomness (``rng``) and sleeping (``sleep``) only
through this module, so a test patches ``services.neurocomment._seams.<name>``
once and every submodule observes it. Mirrors ``services.warming._seams``.
"""

from __future__ import annotations

import random
from asyncio import CancelledError, sleep
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

from core.db import fetch_account, fetch_warming_state, list_warming_account_ids
from core.gemini import generate_text as _generate_text
from core.openai import generate_text as _generate_text_openai
from core.openai import generate_text_deepseek as _generate_text_deepseek
from core.telegram_client import download_post_image as _download_post_image
from core.telegram_client import execute as _gateway_execute
from core.telegram_client import execute_read as _gateway_execute_read
from core.telemetr import search_catalog as search_telemetr
from services.spam_status import refresh_spam_status as _refresh_spam_status

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator

    from pydantic import BaseModel

    from schemas.gemini import GeminiRequest, GeminiResult
    from schemas.spam_status import SpamStatusVerdict
    from schemas.telegram_actions import (
        ActionResult,
        PostImageResult,
        TelegramAction,
        TelegramReadAction,
    )


_GENERATION_CURRENT: ContextVar[Callable[[], bool] | None] = ContextVar(
    "neurocomment_generation_current",
    default=None,
)


class NeurocommentLeaseRevokedError(RuntimeError):
    """A stopped runtime generation attempted external I/O."""


class NeurocommentAccountUnavailableError(NeurocommentLeaseRevokedError):
    """An absent account or one handed to warming attempted external I/O."""


class NeurocommentAccountDeletedError(NeurocommentAccountUnavailableError):
    """The account row itself is gone, so no later attempt can ever succeed.

    Its parent covers the transient refusals too — an account inside a warming cycle,
    or one caught between ``promoted_to_nc`` and ``nc_handed_off``. Both of those end
    on their own, so a post refused by them is still ours to send later; only a
    deleted row is worth settling a post against for good. Callers that merely need
    "this account cannot act right now" keep catching the parent.
    """


class NeurocommentDispatchOwnershipLostError(NeurocommentLeaseRevokedError):
    """The durable claim was lost before Telegram dispatch began."""


class NeurocommentPreDispatchError(RuntimeError):
    """External admission failed before the durable dispatch boundary."""


class NeurocommentPreDispatchCancelledError(CancelledError):
    """The task was cancelled before the durable dispatch boundary."""


class NeurocommentLeaseLostAfterDispatchError(NeurocommentLeaseRevokedError):
    """A Telegram write returned after its runtime generation was revoked."""


@contextmanager
def generation_scope(is_current: Callable[[], bool]) -> Iterator[None]:
    """Bind a background runtime task to its listener generation."""
    token = _GENERATION_CURRENT.set(is_current)
    try:
        yield
    finally:
        _GENERATION_CURRENT.reset(token)


def _assert_live_generation() -> None:
    is_current = _GENERATION_CURRENT.get()
    if is_current is not None and not is_current():
        raise NeurocommentLeaseRevokedError


async def _account_is_available(account_id: str) -> bool:
    return await fetch_account(account_id) is not None and account_id not in (
        await list_warming_account_ids()
    )


async def _account_is_deleted(account_id: str) -> bool:
    """Is the refusal permanent? Read only once admission has already failed."""
    return await fetch_account(account_id) is None


async def _account_is_handed_to_neurocomment(account_id: str) -> bool:
    state = await fetch_warming_state(account_id)
    return state is not None and state.promoted_to_nc and state.nc_handed_off


async def _pre_dispatch_call(call: Callable[[], Awaitable[bool]]) -> bool:
    try:
        return await call()
    except CancelledError as exc:
        raise NeurocommentPreDispatchCancelledError from exc
    except Exception as exc:
        raise NeurocommentPreDispatchError from exc


async def _unavailable_error(account_id: str) -> NeurocommentAccountUnavailableError:
    """Name the refusal an unavailable account just made, permanent or transient."""
    if await _pre_dispatch_call(lambda: _account_is_deleted(account_id)):
        return NeurocommentAccountDeletedError(account_id)
    return NeurocommentAccountUnavailableError(account_id)


async def _admit_write(
    account_id: str,
    before_dispatch: Callable[[], Awaitable[bool]] | None,
) -> None:
    _assert_live_generation()
    if not await _pre_dispatch_call(lambda: _account_is_available(account_id)):
        # One extra read, on the failure path only, buys the caller the difference
        # between "gone" and "busy" — which is the difference between settling the
        # post for good and handing it back to the durable retry ladder.
        raise await _unavailable_error(account_id)
    if before_dispatch is None:
        return
    if not await _pre_dispatch_call(lambda: _account_is_handed_to_neurocomment(account_id)):
        raise NeurocommentAccountUnavailableError(account_id)
    if not await _pre_dispatch_call(before_dispatch):
        raise NeurocommentDispatchOwnershipLostError(account_id)


async def execute(
    account_id: str,
    action: TelegramAction,
    *,
    before_dispatch: Callable[[], Awaitable[bool]] | None = None,
) -> ActionResult:
    """Fence every Telegram write against Stop, warming handoff, and account deletion."""
    from services.warming import account_lock  # noqa: PLC0415

    boundary_crossed = False
    try:
        async with account_lock(account_id):
            await _admit_write(account_id, before_dispatch)
            boundary_crossed = True
            result = await _gateway_execute(account_id, action, domain="neurocomment")
            try:
                _assert_live_generation()
            except NeurocommentLeaseRevokedError as exc:
                raise NeurocommentLeaseLostAfterDispatchError from exc
            return result
    except CancelledError as exc:
        if not boundary_crossed:
            raise NeurocommentPreDispatchCancelledError from exc
        raise


_FENCED_EXECUTE = execute


async def execute_comment(
    account_id: str,
    action: TelegramAction,
    before_dispatch: Callable[[], Awaitable[bool]],
) -> ActionResult:
    """Dispatch a comment with its durable boundary inside the lifecycle lock.

    Tests historically patch :func:`execute`; honour that seam while still advancing
    their in-memory/database boundary. Production keeps the boundary immediately before
    the real gateway call under the shared account lock.
    """
    if execute is not _FENCED_EXECUTE:
        owns_dispatch = await _pre_dispatch_call(before_dispatch)
        if not owns_dispatch:
            raise NeurocommentDispatchOwnershipLostError(account_id)
        return await execute(account_id, action)
    return await _FENCED_EXECUTE(
        account_id,
        action,
        before_dispatch=before_dispatch,
    )


async def execute_read(account_id: str, action: TelegramReadAction) -> BaseModel:
    """Fence Telegram reads with the same account lifecycle boundary as writes."""
    from services.warming import account_lock  # noqa: PLC0415

    async with account_lock(account_id):
        _assert_live_generation()
        if not await _account_is_available(account_id):
            raise NeurocommentAccountUnavailableError(account_id)
        result = await _gateway_execute_read(account_id, action)
        _assert_live_generation()
        return result


async def download_post_image(
    account_id: str,
    channel: str,
    post_id: int,
    max_bytes: int,
) -> PostImageResult:
    """Fence the image read, including a handoff that lands while it is in flight."""
    from services.warming import account_lock  # noqa: PLC0415

    async with account_lock(account_id):
        _assert_live_generation()
        if not await _account_is_available(account_id):
            raise NeurocommentAccountUnavailableError(account_id)
        result = await _download_post_image(account_id, channel, post_id, max_bytes)
        _assert_live_generation()
        return result


async def generate_text(request: GeminiRequest) -> GeminiResult:
    _assert_live_generation()
    result = await _generate_text(request)
    _assert_live_generation()
    return result


async def generate_text_deepseek(request: GeminiRequest) -> GeminiResult:
    _assert_live_generation()
    result = await _generate_text_deepseek(request)
    _assert_live_generation()
    return result


async def generate_text_openai(request: GeminiRequest) -> GeminiResult:
    _assert_live_generation()
    result = await _generate_text_openai(request)
    _assert_live_generation()
    return result


async def refresh_spam_status(account_id: str, *, force: bool = False) -> SpamStatusVerdict:
    from services.warming import account_lock  # noqa: PLC0415

    async with account_lock(account_id):
        _assert_live_generation()
        if not await _account_is_available(account_id):
            raise NeurocommentAccountUnavailableError(account_id)
        result = await _refresh_spam_status(account_id, force=force)
        _assert_live_generation()
        return result


# Bound once here so every gateway event this domain triggers is named
# ``neurocomment_telegram_*`` and shows up in the neurocomment feed
# (``event_prefix=neurocomment``) instead of only in warming's card.
# ``execute_read`` takes no domain because none of the read actions this domain issues
# logs anything today. The read path does log in two places, but both are reached only
# from accounts-page reads: ``telegram_list_profile_music_unsupported``
# (``_read_profile.py``, from ``ListProfileMusic``) and ``telegram_thumb_download_flood_wait``
# (``_thumbs.py``, from the profile-photo and story thumbnail batches). Neurocomment's
# reads are discovery/qualification lookups, which are silent.
# SystemRandom: non-cryptographic jitter/selection; avoids ruff S311 on the
# module-level ``random.*`` helpers. Behaviour is identical for our needs.
rng = random.SystemRandom()

__all__ = [
    "NeurocommentAccountDeletedError",
    "NeurocommentAccountUnavailableError",
    "NeurocommentDispatchOwnershipLostError",
    "NeurocommentLeaseLostAfterDispatchError",
    "NeurocommentLeaseRevokedError",
    "NeurocommentPreDispatchCancelledError",
    "NeurocommentPreDispatchError",
    "download_post_image",
    "execute",
    "execute_comment",
    "execute_read",
    "generate_text",
    "generate_text_deepseek",
    "generate_text_openai",
    "generation_scope",
    "refresh_spam_status",
    "rng",
    "search_telemetr",
    "sleep",
]
