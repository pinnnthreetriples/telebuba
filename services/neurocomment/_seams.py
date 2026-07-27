"""Injectable seams, centralised so tests patch them in one place.

The neurocomment domain reaches Telegram (``execute`` / ``execute_read``),
Gemini (``generate_text``), Telemetr.io (``search_telemetr``), the spam probe
(``refresh_spam_status``), randomness (``rng``) and sleeping (``sleep``) only
through this module, so a test patches ``services.neurocomment._seams.<name>``
once and every submodule observes it. Mirrors ``services.warming._seams``.
"""

from __future__ import annotations

import random
from asyncio import sleep
from functools import partial

from core.gemini import generate_text
from core.openai import generate_text as generate_text_openai
from core.telegram_client import execute as _gateway_execute
from core.telegram_client import execute_read
from core.telemetr import search_catalog as search_telemetr
from services.spam_status import refresh_spam_status

# Bound once here so every gateway event this domain triggers is named
# ``neurocomment_telegram_*`` and shows up in the neurocomment feed
# (``event_prefix=neurocomment``) instead of only in warming's card.
# ``execute_read`` takes no domain because none of the read actions this domain issues
# logs anything today. The read path does log in two places, but both are reached only
# from accounts-page reads: ``telegram_list_profile_music_unsupported``
# (``_read_profile.py``, from ``ListProfileMusic``) and ``telegram_thumb_download_flood_wait``
# (``_thumbs.py``, from the profile-photo and story thumbnail batches). Neurocomment's
# reads are discovery/qualification lookups, which are silent.
execute = partial(_gateway_execute, domain="neurocomment")

# SystemRandom: non-cryptographic jitter/selection; avoids ruff S311 on the
# module-level ``random.*`` helpers. Behaviour is identical for our needs.
rng = random.SystemRandom()

__all__ = [
    "execute",
    "execute_read",
    "generate_text",
    "generate_text_openai",
    "refresh_spam_status",
    "rng",
    "search_telemetr",
    "sleep",
]
