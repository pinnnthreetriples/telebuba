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
    ``channel_id`` rides along when a ``channel_create`` failed AFTER the
    channel was created (post-create username refusal): the channel exists as
    private, so the UI can adopt it instead of re-creating a duplicate.
    """

    def __init__(
        self,
        code: str,
        *,
        retry_after_seconds: int | None = None,
        channel_id: str | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.retry_after_seconds = retry_after_seconds
        self.channel_id = channel_id


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
    raise AccountActionError(
        code,
        retry_after_seconds=result.flood_wait_seconds,
        channel_id=result.channel_id,
    )
