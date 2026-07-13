"""Shared ``ActionResult`` → domain-error mapping for the accounts service."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from schemas.telegram_actions import ActionResult

__all__ = ["AccountActionError", "raise_for_result"]


class AccountActionError(ValueError):
    """A Telegram action was refused.

    ``str(exc)`` is the stable, locale-neutral code (the SPA translates it —
    non-negotiable #12). For the flood family it also carries the
    server-mandated ``retry_after_seconds`` so the API error envelope can tell
    the client how long to wait instead of dropping the duration.
    """

    def __init__(self, code: str, *, retry_after_seconds: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.retry_after_seconds = retry_after_seconds


def raise_for_result(result: ActionResult) -> None:
    """Raise :class:`AccountActionError` unless ``result`` is ``ok``."""
    if result.status == "ok":
        return
    if result.status == "unavailable":
        # Infrastructure failure (pool/socket) — keep the stable status code,
        # not the raw exception message, so the API maps it to 503 unavailable
        # instead of billing an internal outage as a 400 client fault.
        code = "unavailable"
        raise AccountActionError(code)
    code = result.error_message or result.status
    raise AccountActionError(code, retry_after_seconds=result.flood_wait_seconds)
