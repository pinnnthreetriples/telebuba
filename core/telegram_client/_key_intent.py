"""The key-intent journal as the gateway flows use it: never in the way of the flow itself.

The journal (``core.repositories.account_key_intents``) is written so a crash in the middle
of a flow that makes a new auth key can be repaired afterwards. A journal write that fails
must not turn a working sign-in into a failed one, so every write after the first goes
through the ``*_quietly`` helpers: they log the exception class only and never raise.

:func:`sign_in_intent` is the small wrapper of the plain sign-ins (code, QR, passkey) that
make a new key with the new API: the API is recorded only after the sign-in, and a crash in
between would connect the key with the default API for ever.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from core.repositories.account_key_intents import advance_intent, close_intent, open_intent
from core.repositories.device_fingerprint import fetch_device_fingerprint

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from schemas.device_fingerprint import TelegramClientProfile
    from schemas.key_intent import KeyIntentOutcome, KeyIntentStage

# Stdlib sink: a transport failure stringifies with the proxy URL (see ``_pool``).
logger = logging.getLogger(__name__)


async def advance_quietly(
    intent_id: int | None,
    *,
    stage: KeyIntentStage | None = None,
    new_hash: str | None = None,
    old_hash: str | None = None,
) -> None:
    """Record progress; a journal that cannot be written is logged and ignored."""
    if intent_id is None:
        return
    try:
        await advance_intent(intent_id, stage=stage, new_hash=new_hash, old_hash=old_hash)
    except Exception as exc:  # noqa: BLE001 - the flow goes on; the repair works with less.
        logger.warning("key intent not advanced (%s)", type(exc).__name__)


async def close_quietly(intent_id: int | None, outcome: KeyIntentOutcome) -> None:
    """Close the intent; a journal that cannot be written is logged and ignored."""
    if intent_id is None:
        return
    try:
        await close_intent(intent_id, outcome)
    except Exception as exc:  # noqa: BLE001 - an intent left open is repaired later.
        logger.warning("key intent not closed (%s)", type(exc).__name__)


async def open_quietly(
    *,
    account_id: str,
    device_type: str,
    session_name: str,
    api_id: int | None,
    device_json: str | None,
) -> int | None:
    """Journal a second device's sign-in; ``None`` (logged) when the journal cannot be written.

    The caller does not sign in without it (see ``_second_device``).
    """
    try:
        return await open_intent(
            account_id=account_id,
            kind="second_device",
            device_type=device_type,
            session_name=session_name,
            api_id=api_id,
            device_json=device_json,
        )
    except Exception as exc:  # noqa: BLE001 - reported to the caller as ``None``.
        logger.warning("second-device intent not opened (%s)", type(exc).__name__)
        return None


async def _open_sign_in(profile: TelegramClientProfile, *, new_key: bool) -> int | None:
    """Open a ``sign_in`` intent when this sign-in makes a key with an API not yet stored."""
    api_id = getattr(getattr(profile, "device", None), "api_id", None)
    if not new_key or api_id is None:
        return None
    try:
        stored = await fetch_device_fingerprint(profile.account_id, profile.device_type)
        if stored is not None and stored.api_id == api_id:
            return None
        return await open_intent(
            account_id=profile.account_id,
            kind="sign_in",
            device_type=profile.device_type,
            session_name=Path(profile.session_path).name,
            api_id=api_id,
        )
    except Exception as exc:  # noqa: BLE001 - without a journal the sign-in is as it was.
        logger.warning("sign-in intent not opened (%s)", type(exc).__name__)
        return None


async def _settle_sign_in(intent_id: int, profile: TelegramClientProfile) -> None:
    """Close the intent only when the key's API is on record; otherwise leave it open.

    A sign-in that landed while its answer was lost, a cancellation, or a failed write of
    the API all end the flow with the key made but its API not stored. The repair
    (``services.accounts._key_repair``) settles an open intent from the files; closing it
    here would leave nothing to repair.
    """
    api_id = getattr(getattr(profile, "device", None), "api_id", None)
    try:
        stored = await fetch_device_fingerprint(profile.account_id, profile.device_type)
    except Exception as exc:  # noqa: BLE001 - unknown is not "recorded": the repair decides.
        logger.warning("sign-in intent left open (%s)", type(exc).__name__)
        return
    if stored is not None and stored.api_id == api_id:
        await close_quietly(intent_id, "done")


@contextlib.asynccontextmanager
async def sign_in_intent(profile: TelegramClientProfile, *, new_key: bool) -> AsyncIterator[None]:
    """Journal a sign-in that makes a new key; settled, shielded, however the flow ends."""
    intent_id = await _open_sign_in(profile, new_key=new_key)
    try:
        yield
    finally:
        if intent_id is not None:
            await asyncio.shield(_settle_sign_in(intent_id, profile))
