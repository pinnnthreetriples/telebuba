"""Injectable engine seams, centralised so tests patch them in one place.

The warming engine reaches Telegram (``execute``), Gemini (``generate_text``),
the spam probe (``refresh_spam_status``) and randomness (``_rng``) only through
this module, so a test patches ``services.warming._seams.<name>`` once and every
engine submodule observes it. Before the package split these lived directly on
the ``services.warming`` namespace; the seam module preserves single-point
patching across the split submodules.
"""

from __future__ import annotations

import asyncio
import random

from core.gemini import generate_text
from core.telegram_client import execute
from services.spam_status import refresh_spam_status

# SystemRandom: non-cryptographic jitter/selection; avoids ruff S311 on the
# module-level ``random.*`` helpers. Behaviour is identical for our needs.
rng = random.SystemRandom()


async def sleep(seconds: float) -> None:
    """Async sleep seam — patched to a no-op in tests so delays stay instant."""
    await asyncio.sleep(seconds)


__all__ = ["execute", "generate_text", "refresh_spam_status", "rng", "sleep"]
