"""Shared ``ActionResult`` → domain-error mapping for the accounts service."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.telegram_client import TelegramReadError
    from schemas.telegram_actions import ActionResult

__all__ = [
    "AccountActionError",
    "AccountNotFoundError",
    "action_error_for_read",
    "raise_for_result",
]


class AccountNotFoundError(LookupError):
    """No such account row — the API must answer 404, not 400.

    A ``LookupError`` on purpose, NOT part of the ``ValueError`` family: that is
    what lets ``api.v1._errors.service_errors_to_http`` map it to 404 without
    being swallowed by the generic ``ValueError`` → 400 collapse.
    """


class AccountActionError(ValueError):
    """A Telegram action was refused.

    ``str(exc)`` is always a bounded, locale-neutral code — never third-party
    prose (non-negotiable #12). It is either a gateway stable code the SPA
    translates under ``accounts.*.code.*`` (``username_occupied``,
    ``story_image_invalid``, …) or, when the refusal came from an exception whose
    message is not a contract, the ``ActionStatus`` itself (``failed``,
    ``unavailable``, the flood family). For the flood family it also carries the
    server-mandated ``retry_after_seconds`` so the API error envelope can tell
    the client how long to wait instead of dropping the duration.
    ``channel_id`` rides along when a ``channel_create`` failed AFTER the
    channel was created (post-create username refusal): the channel exists as
    private, so the UI can adopt it instead of re-creating a duplicate.
    ``applied_privacy_keys`` does the same for a partially-applied privacy write:
    ``account.setPrivacy`` is one call per key with no rollback, so the keys that
    DID land must be reported or the operator is told the account is untouched
    while its avatar is already public.
    """

    def __init__(
        self,
        code: str,
        *,
        retry_after_seconds: int | None = None,
        channel_id: str | None = None,
        applied_privacy_keys: list[str] | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.retry_after_seconds = retry_after_seconds
        self.channel_id = channel_id
        self.applied_privacy_keys = applied_privacy_keys


# Gateway exception classes constructed WITH a stable code, so their ``str(exc)``
# — and therefore ``ActionResult.error_message`` — IS the contract the SPA
# translates. Every other exception reaching ``_generic_error`` carries
# third-party prose (Telethon English, Pillow reasons) that must never become a
# "code". ``error_type`` (the class name) is the only discriminator
# ``ActionResult`` carries, so the set is pinned by name rather than by import —
# ``tests/services/accounts/test_result.py`` keeps it honest against the classes.
_STABLE_CODE_ERROR_TYPES: frozenset[str] = frozenset(
    {
        "ChannelGatewayError",
        "ProfileGatewayError",
        "StoryCollageLayoutError",
        "StoryImageNormalisationError",
        "StoryVideoNormalisationError",
    },
)


def raise_for_result(result: ActionResult) -> None:
    """Raise :class:`AccountActionError` unless ``result`` is ``ok``.

    The code is always bounded: a gateway stable code when the failing exception
    was one of ours, otherwise the ``ActionStatus`` literal. Third-party
    exception messages stay in the failure log and never become the code.
    """
    if result.status == "ok":
        return
    if result.status == "unavailable":
        # Infrastructure failure (pool/socket) — keep the stable status code,
        # not the raw exception message, so the API maps it to 503 unavailable
        # instead of billing an internal outage as a 400 client fault.
        code = "unavailable"
        raise AccountActionError(code, applied_privacy_keys=result.applied_privacy_keys)
    stable = result.error_message if result.error_type in _STABLE_CODE_ERROR_TYPES else None
    raise AccountActionError(
        stable or result.status,
        retry_after_seconds=result.flood_wait_seconds,
        channel_id=result.channel_id,
        applied_privacy_keys=result.applied_privacy_keys,
    )


def action_error_for_read(exc: TelegramReadError, residual_code: str) -> AccountActionError:
    """Map a refused gateway READ onto a stable code, keeping the flood duration.

    Reads used to flatten every refusal into one caller-supplied code, so a
    flood-waited account was reported with no duration and an infrastructure outage
    was billed as a 400 client fault — both facts the write path already reports.
    ``TelegramReadError.kind`` is the gateway's own classification, so this needs no
    string parsing; ``residual_code`` stays the answer for everything else.
    """
    if exc.kind == "flood_wait":
        return AccountActionError("flood_wait", retry_after_seconds=exc.seconds)
    if exc.kind == "unavailable":
        return AccountActionError("unavailable")
    return AccountActionError(residual_code)
