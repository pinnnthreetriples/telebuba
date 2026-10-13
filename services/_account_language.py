"""Which language an account speaks: its phone country's.

The same one its Telegram client announces (``core.device_fingerprint_lang``), so the
channels it reads, the chat it writes and the partners it is paired with all agree
with what the client says it speaks. Outside ``services.warming`` because dialogue
pairing needs it too and that package imports the pairing module.
"""

from __future__ import annotations

from core.device_fingerprint_lang import language_pair
from core.phone_geo import country_for_phone


def account_language(phone: str | None) -> str | None:
    """The phone country's language, ``None`` without a usable number.

    ``language_pair`` falls back to ``en`` for a missing number; here that would steer
    an unknown account onto English channels, so no country means no language.
    """
    if country_for_phone(phone) is None:
        return None
    return language_pair(phone)[0]
