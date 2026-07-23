"""Pre-read multipart size guard shared by the accounts upload routes.

Rejecting an over-cap upload *before* ``await file.read()`` prevents the real RAM
blow-up: ``file.read()`` materializing the whole part as a single ``bytes`` object.
It does NOT prevent the transfer itself — Starlette's multipart parser has already
received the body and spooled it (``SpooledTemporaryFile``: ~1 MB in RAM, then
disk) by the time the handler runs, and ``.size`` is the final measured byte count.
So this guard caps peak memory, not bandwidth/disk (a Content-Length middleware
would be needed for that). When ``.size`` is unavailable (``None``) we skip and let
the service-layer size check reject after the read — kept as defense-in-depth.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException
from fastapi import status as http_status

if TYPE_CHECKING:
    from fastapi import UploadFile


def reject_oversized_upload(file: UploadFile, *, max_bytes: int, detail: str) -> None:
    """Raise 400 with ``detail`` if the multipart part is known to exceed ``max_bytes``."""
    if file.size is not None and file.size > max_bytes:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=detail)
