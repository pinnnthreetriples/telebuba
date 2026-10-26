"""Parse the seller's session JSON (the file sold beside a ``.session``).

Pure: bytes in, :class:`SellerSessionMeta` out, no I/O. The real seller keys are
``twoFA`` (cloud password), ``device`` (``Samsung Galaxy S23``), ``sdk``
(``Android 14``), ``app_version``, ``lang_code``, ``system_lang_code``, ``phone``
and ``id``; a few synonyms are accepted and every other key is ignored.

The password never leaves this module except inside the returned model, whose
``password`` field is excluded from ``repr``. Errors carry a stable code only.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from core.device_fingerprint_lang import language_pair
from schemas.device_fingerprint import DeviceFingerprint
from schemas.session_json import SellerSessionMeta

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["SessionJsonError", "fingerprint_from_meta", "parse_seller_json"]

_MAX_JSON_BYTES = 64 * 1024
_NON_DIGITS = re.compile(r"\D")

# First present, non-empty key wins; the seller's own spelling comes first.
_PASSWORD_KEYS = ("twoFA", "password", "2fa", "twofa", "2FA", "twoFa")
_DEVICE_KEYS = ("device", "device_model")
_SDK_KEYS = ("sdk", "system_version")
_APP_VERSION_KEYS = ("app_version",)
_LANG_KEYS = ("lang_code", "lang_pack")
_SYSTEM_LANG_KEYS = ("system_lang_code", "system_lang_pack")


class SessionJsonError(ValueError):
    """The JSON could not be read; ``str(exc)`` is the stable refusal code."""


def _text(data: Mapping[str, object], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            text = str(value).strip()
            if text:
                return text
    return None


def _user_id(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def parse_seller_json(content: bytes) -> SellerSessionMeta:
    """The seller JSON's useful fields, or :class:`SessionJsonError` ``session_json_invalid``."""
    code = "session_json_invalid"
    if not content or len(content) > _MAX_JSON_BYTES:
        raise SessionJsonError(code)
    try:
        data = json.loads(content.decode("utf-8-sig"))
    except ValueError:
        # ``from None``: the decoder's message can quote the document, password included.
        raise SessionJsonError(code) from None
    if not isinstance(data, dict):
        raise SessionJsonError(code)
    phone = _text(data, ("phone",))
    return SellerSessionMeta(
        password=_text(data, _PASSWORD_KEYS),
        device_model=_text(data, _DEVICE_KEYS),
        system_version=_text(data, _SDK_KEYS),
        app_version=_text(data, _APP_VERSION_KEYS),
        lang_code=_text(data, _LANG_KEYS),
        system_lang_code=_text(data, _SYSTEM_LANG_KEYS),
        phone=(_NON_DIGITS.sub("", phone) or None) if phone else None,
        user_id=_user_id(data.get("id")),
    )


def fingerprint_from_meta(account_id: str, meta: SellerSessionMeta) -> DeviceFingerprint | None:
    """The seller's Android device as stored fingerprint, or ``None`` when not Android.

    Values are kept verbatim (no re-derivation), platform ``android``. A missing
    language falls back to the phone's country, like any minted fingerprint.
    """
    if not (meta.is_android and meta.device_model and meta.system_version and meta.app_version):
        return None
    lang, system_lang = language_pair(meta.phone)
    return DeviceFingerprint(
        account_id=account_id,
        device_type="mobile",
        platform="android",
        device_model=meta.device_model,
        system_version=meta.system_version,
        app_version=meta.app_version,
        lang_code=meta.lang_code or lang,
        system_lang_code=meta.system_lang_code or system_lang,
    )
