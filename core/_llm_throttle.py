"""The inter-request spacing gate both LLM gateways share, one clock per provider.

Per provider because the quota it keeps a burst under is per provider: a DeepSeek
call must not wait out a Gemini one.
"""

from __future__ import annotations

import asyncio
import time


class Throttle:
    """One provider's last-call clock.

    A single lock serialises the wait so concurrent generations queue and fire
    ``min_interval`` apart, keeping a burst under a per-minute API quota.
    """

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.last_call = 0.0  # time.monotonic() of the previous slot

    async def wait(self, min_interval: float) -> None:
        """Sleep until ``min_interval`` has elapsed since this provider's previous call.

        ``min_interval <= 0`` disables the gate entirely (and never touches the
        clock, so callers that opt out don't perturb opted-in spacing).
        """
        if min_interval <= 0:
            return
        async with self.lock:
            wait = self.last_call + min_interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self.last_call = time.monotonic()
