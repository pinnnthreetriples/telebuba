"""Keep an LLM key out of the error strings the HTTP gateways hand back.

``core.gemini`` and ``core.openai`` return ``f"{type(exc).__name__}: {exc}"`` on a
transport failure, and that string is logged — the ``logs`` table, ``GET /events``,
loguru. Some httpx/h11 errors quote the offending header in full (a key with a stray
newline: ``Illegal header value b'Bearer sk-…'``, the newline escaped), so the key has
to be masked before the string leaves the gateway.
"""

from __future__ import annotations

import re

# The key is masked piece by piece: h11 escapes a control character (``\n`` becomes
# the two characters ``\`` ``n``), so the raw key never appears whole in the message —
# its header-safe runs do. The message quotes the header as a bytes repr, which also
# escapes ``\`` and (when both quote kinds occur) ``'``, so those two split runs as well.
# Shorter runs are left alone; they reveal nothing.
_HEADER_SAFE_RUN = re.compile(r"[!-&(-\[\]-~]+")
_MIN_MASKED_RUN = 4


def exception_text(exc: BaseException, api_key: str) -> str:
    """``"<ExcType>: <message>"`` with every sizeable run of ``api_key`` masked."""
    text = f"{type(exc).__name__}: {exc}"
    for run in _HEADER_SAFE_RUN.findall(api_key):
        if len(run) >= _MIN_MASKED_RUN:
            text = text.replace(run, "***")
    return text
