"""Scrub secrets out of a log event's ``extra`` before any logging tier sees it.

``log_event`` used to rely on every caller being careful. This is the one backstop:
pure, recursive, depth-limited. A value under a key that names a secret becomes
``[redacted]`` (everything except ``None``, booleans and empty strings, so flags such as
``twofa_stored: true`` survive), and credentials in URL shapes (``scheme://user:pass@host``,
``user:pass@host:port``, i.e. a proxy URL) are masked in any string whatever its key.

Keys match on ``_``/``-``/camelCase segments; a secret WORD (``password``, ``token`` ...)
also matches inside a glued segment (``oldpassword``, ``csrftoken``). ``code`` (an error
code such as ``session_dead``), a bare ``hash`` and a bare ``key`` are NOT secrets.
Numbers survive only under the token family (LLM usage counters such as
``prompt_tokens``), ``credentials`` (a bag of fields) and size-like names (``password_len``);
everywhere else under a secret key a number is redacted too (a numeric login code is a
secret). Sets are walked.
"""

from __future__ import annotations

import re
from itertools import pairwise
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

REDACTED = "[redacted]"
TRUNCATED = "[truncated]"
_MAX_DEPTH = 6

_NOT_SECRET = 0
_NUMBERS_KEPT = 1  # token family / size-like names: strings are redacted, numbers stay
_SECRET = 2

# A secret word anywhere inside a segment (``oldpassword``, ``csrftoken``).
_SUBSTRING_WORDS = ("password", "passwd", "passphrase", "secret")
# The token family (and ``credentials``, a bag of fields): strings are redacted, numbers stay.
_WEAK_SUBSTRING = "token"
_WEAK_SEGMENTS = frozenset({"credentials"})
# Whole-segment names; plural forms of these (``cookies``) are secrets too.
_SINGLE_WORDS = ("cookie", "authorization", "otp", "pwd", "jwt", "bearer", "apikey")
_SECRET_WORDS = frozenset((*_SINGLE_WORDS, *(f"{word}s" for word in _SINGLE_WORDS)))
# A two-word name must appear as consecutive segments.
_SECRET_PAIRS = frozenset(
    {
        ("api", "hash"),
        ("api", "key"),
        ("auth", "key"),
        ("private", "key"),
        ("session", "string"),
        ("login", "code"),
        ("phone", "code"),
    },
)
_SIZE_WORDS = frozenset({"len", "length", "count", "size", "version"})

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_SEPARATORS = re.compile(r"[_\-\s]+")
# ``scheme://user:pass@host``: a password may hold ``@`` or ``/``, so with a colon the
# password runs up to the last ``@`` of the token; without one the authority ends at ``/``.
_URL_USERINFO = re.compile(
    r"(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]{0,31}://)"
    r"(?:[^\s/?#@:]{1,128}:[^\s]{1,512}|[^\s/?#]{1,512})@"
)
# ``user:pass@host`` / ``//user:pass@host`` with no scheme. A colon-separated credential
# is required, so ordinary ``name@host`` text is left alone.
_SCHEMELESS_USERINFO = re.compile(
    r"(?P<lead>^|[\s(\[,;=\"']|//)[^\s:@/]{1,128}:[^\s/]{1,256}@(?=[\w\[])"
)


def _key_mode(key: object) -> int:
    segments = [
        part.lower() for part in _SEPARATORS.split(_CAMEL_BOUNDARY.sub("_", str(key))) if part
    ]
    strong = (
        any(segment in _SECRET_WORDS for segment in segments)
        or any(pair in _SECRET_PAIRS for pair in pairwise(segments))
        or any(word in segment for segment in segments for word in _SUBSTRING_WORDS)
    )
    weak = any(_WEAK_SUBSTRING in segment or segment in _WEAK_SEGMENTS for segment in segments)
    if not strong and not weak:
        return _NOT_SECRET
    if not strong or _SIZE_WORDS.intersection(segments):
        return _NUMBERS_KEPT
    return _SECRET


def _scrub_text(value: str, *, mode: int) -> str:
    if not value:
        return value
    if mode:
        return REDACTED
    masked = _URL_USERINFO.sub(r"\g<scheme>***@", value)
    return _SCHEMELESS_USERINFO.sub(r"\g<lead>***@", masked)


def _scrub_container(value: dict | list | tuple | set | frozenset, depth: int, mode: int) -> object:
    if depth >= _MAX_DEPTH:
        return TRUNCATED
    if isinstance(value, dict):
        return {
            key: _scrub(item, depth + 1, max(mode, _key_mode(key))) for key, item in value.items()
        }
    if isinstance(value, (set, frozenset)):
        try:
            value = sorted(value)  # type: ignore[type-var]
        except TypeError:
            value = list(value)
    items = [_scrub(item, depth + 1, mode) for item in value]
    return tuple(items) if isinstance(value, tuple) else items


def _scrub(value: object, depth: int, mode: int) -> object:
    if isinstance(value, str):
        return _scrub_text(value, mode=mode)
    if isinstance(value, (dict, list, tuple, set, frozenset)):
        return _scrub_container(value, depth, mode)
    if value is None or isinstance(value, bool):
        return value
    if mode == _NUMBERS_KEPT and isinstance(value, (int, float)):
        return value
    if mode:
        return value if isinstance(value, (bytes, bytearray)) and not value else REDACTED
    return value


def scrub_extra(extra: Mapping[str, object]) -> dict[str, object]:
    """A copy of ``extra`` with secret values redacted and URL userinfo masked."""
    return {key: _scrub(item, 1, _key_mode(key)) for key, item in extra.items()}
