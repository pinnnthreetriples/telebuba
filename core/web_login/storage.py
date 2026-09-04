"""Build the web.telegram.org/k/ (WebK) localStorage map for a cold authorized boot.

This mirrors an internal, deploy-versioned storage format of WebK
(github.com/morethanwords/tweb). If web.telegram.org changes how it persists a
session, this map must be updated. Every key/value below was verified against
tweb's own source at https://raw.githubusercontent.com/morethanwords/tweb/master/ :

- Serialization: ``src/lib/localStorage.ts`` — ``set()`` stores ``JSON.stringify(value)``
  and ``get()`` does ``JSON.parse``. So string values are stored WITH surrounding
  double quotes and numbers without. We reproduce that with :func:`json.dumps`.
- Hex format: ``src/lib/appManagers/apiManager.ts`` writes ``dcN_auth_key`` as
  ``bytesToHex(key)`` and ``dcN_server_salt`` as ``bytesToHex(salt)``.
  ``src/helpers/bytes/bytesToHex.ts`` is LOWERCASE (its ``uppercase`` flag is not
  passed here) and 2-char zero-padded — so 256 bytes -> 512 lowercase hex chars,
  8 bytes -> 16. (The client hex-decodes case-insensitively via ``bytesFromHex``.)
- Fingerprint: ``src/lib/sessionStorage.ts`` and ``src/lib/accounts/accountController.ts``
  define ``auth_key_fingerprint = dc{baseDcId}_auth_key.slice(0, 8)`` — the first
  8 chars of the home-DC auth-key hex STRING (so it is case-consistent with it).
- Which keys boot authorized: ``vite.preview.config.ts`` seeds exactly this set to
  make a real WebK build boot logged in — both the modern ``account1`` object and
  the legacy top-level keys (WebK migrates the latter into the former). We emit
  both, byte-for-byte as that seeder does.
- ``user_auth`` shape: ``{date, id, dcID}`` (uppercase ``dcID``); ``id``/``userId``
  are numeric (``PeerId`` is a number in tweb) — per ``vite.preview.config.ts`` and
  ``src/lib/accounts/types.d.ts``.

If ``server_salt`` is unknown (``None``), the salt key is OMITTED; WebK then falls
back to its ``'AAAAAAAAAAAAAAAA'`` default and re-negotiates on the first request
via the normal ``BAD_SERVER_SALT`` self-heal.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.telegram_client._web_login import MintedWebAuth


def _dumps(value: object) -> str:
    """JSON like WebK's ``JSON.stringify``: compact separators, so values match."""
    return json.dumps(value, separators=(",", ":"))


def build_webk_localstorage(auth: MintedWebAuth) -> dict[str, str]:
    """Return the localStorage ``key -> stored-string`` map that boots WebK authorized.

    Values are already in the exact form to hand to ``localStorage.setItem`` (a
    quoted hex string is returned WITH its surrounding quotes). ``user_auth.date``
    is the only clock-derived field (login timestamp, seconds), matching what the
    real login writer records.
    """
    dc = auth.dc_id
    key_hex = auth.auth_key.hex()  # lowercase, 512 chars (see module docstring)
    fingerprint = key_hex[:8]

    account: dict[str, object] = {
        "userId": auth.user_id,
        "dcId": dc,
        f"dc{dc}_auth_key": key_hex,
    }
    store: dict[str, str] = {
        "dc": _dumps(dc),
        f"dc{dc}_auth_key": _dumps(key_hex),
        "user_auth": _dumps({"date": int(time.time()), "id": auth.user_id, "dcID": dc}),
        "auth_key_fingerprint": _dumps(fingerprint),
        "server_time_offset": _dumps(0),
    }
    if auth.server_salt is not None:
        salt_hex = auth.server_salt.hex()  # lowercase, 16 chars
        store[f"dc{dc}_server_salt"] = _dumps(salt_hex)
        account[f"dc{dc}_server_salt"] = salt_hex

    account["auth_key_fingerprint"] = fingerprint
    store["account1"] = _dumps(account)
    return store
