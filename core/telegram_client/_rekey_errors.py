"""The re-key's refusals: one bounded code per way it can end, never Telethon prose.

Split out of ``_rekey`` (file size).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from telethon import errors

from core.telegram_client._pool import TelegramClientPoolError
from core.telegram_client._profile import _DEAD_SESSION_ERRORS
from core.twofa_budget import TwofaAttemptsExhaustedError

if TYPE_CHECKING:
    from schemas.session_rekey import RekeyRefusalCode

# Stdlib sink: a transport failure stringifies with the proxy URL (see ``_pool``).
logger = logging.getLogger(__name__)

_APPROVER: RekeyRefusalCode = "rekey_approver_unavailable"
_TOKEN: RekeyRefusalCode = "rekey_token_rejected"  # noqa: S105 - a refusal code
_TWOFA_MISSING: RekeyRefusalCode = "rekey_twofa_missing"
_TWOFA_INVALID: RekeyRefusalCode = "rekey_twofa_invalid"
_TWOFA_EXHAUSTED: RekeyRefusalCode = "twofa_attempts_exhausted"
_MISMATCH: RekeyRefusalCode = "rekey_user_mismatch"
_CONFLICT: RekeyRefusalCode = "rekey_session_conflict"
_SWAP: RekeyRefusalCode = "rekey_swap_failed"
_FAILED: RekeyRefusalCode = "rekey_failed"
_FLOOD: RekeyRefusalCode = "rekey_flood_wait"
_TIMEOUT: RekeyRefusalCode = "rekey_timeout"
_UNAVAILABLE: RekeyRefusalCode = "rekey_unavailable"
_DEAD: RekeyRefusalCode = "rekey_session_dead"
_OLD_UNKNOWN: RekeyRefusalCode = "rekey_old_key_unknown"
_INCOMPLETE: RekeyRefusalCode = "rekey_incomplete"


class RekeyGatewayError(Exception):
    """The re-key was refused or failed; ``str(exc)`` is the stable code, never Telethon prose."""

    def __init__(
        self,
        code: str,
        *,
        retry_after_seconds: int | None = None,
        error_type: str | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.retry_after_seconds = retry_after_seconds
        self.error_type = error_type
        # Only for ``rekey_incomplete``: the old authorization, which must NOT be ended now.
        self.old_hash: str | None = None


def _refusal(code: str, error_type: str | None = None) -> RekeyGatewayError:
    return RekeyGatewayError(code, error_type=error_type)


# Telegram answers that end the attempt, each with the code the operator is shown.
_ERROR_CODES: tuple[tuple[tuple[type[Exception], ...], str], ...] = (
    ((errors.PasswordHashInvalidError,), _TWOFA_INVALID),
    ((TwofaAttemptsExhaustedError,), _TWOFA_EXHAUSTED),
    (
        (
            errors.AuthTokenExpiredError,
            errors.AuthTokenInvalidxError,
            errors.AuthTokenAlreadyAcceptedError,
        ),
        _TOKEN,
    ),
    # ``TimeoutError`` is an ``OSError``: it must be told apart from a dropped connection, because
    # the login token was accepted and Telegram's answer never came, not the network failing.
    ((TimeoutError,), _TIMEOUT),
    ((TelegramClientPoolError, ConnectionError), _UNAVAILABLE),
)


def _classify(exc: Exception) -> RekeyGatewayError:
    """The bounded code for whatever stopped the login or the swap."""
    if isinstance(exc, RekeyGatewayError):
        return exc
    name = type(exc).__name__
    if isinstance(exc, errors.FloodWaitError):
        return RekeyGatewayError(_FLOOD, retry_after_seconds=exc.seconds, error_type=name)
    if isinstance(exc, _DEAD_SESSION_ERRORS):
        return _refusal(_DEAD, name)
    for error_types, code in _ERROR_CODES:
        if isinstance(exc, error_types):
            return _refusal(code, name)
    logger.warning("session re-key failed for an account", exc_info=exc)
    return _refusal(_FAILED, name)
