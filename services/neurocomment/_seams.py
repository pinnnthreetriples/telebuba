"""Injectable seams, centralised so tests patch them in one place.

The neurocomment onboarding reaches Telegram (``execute`` / ``execute_read``)
and randomness (``rng``) only through this module, so a test patches
``services.neurocomment._seams.<name>`` once and every submodule observes it.
Mirrors ``services.warming._seams``.
"""

from __future__ import annotations

import random

from core.telegram_client import execute, execute_read

# SystemRandom: non-cryptographic jitter; avoids ruff S311 on the module-level
# ``random.*`` helpers. Behaviour is identical for our needs.
rng = random.SystemRandom()

__all__ = ["execute", "execute_read", "rng"]
