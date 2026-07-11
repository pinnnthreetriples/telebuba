"""Transient SSE nudges for the neurocomment runtime.

A "signal" here is a bus-only frame that is deliberately *not* persisted (contrast
``core.logging.log_event``): it exists solely to trigger the SPA's existing
SSE → invalidate → refetch pipeline, so the board refreshes live without writing a
row to the event log. Kept in its own tiny module so ``_runtime``/``onboarding``
stay within the file-size gate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.logging import signal_event

if TYPE_CHECKING:
    from schemas.neurocomment_progress import OnboardingProgressEvent


def signal_onboarding_progress(_event: OnboardingProgressEvent) -> None:
    """Wire onboarding progress to a transient SSE nudge so the board refreshes live.

    Passed as ``on_progress`` at production onboarding triggers. The payload is
    irrelevant — the SPA re-reads the board over HTTP; this just publishes a
    "refresh now" frame via the non-persisted path, so a frame per channel-join
    never floods the event log.
    """
    signal_event("neurocomment_onboarding_progress")
