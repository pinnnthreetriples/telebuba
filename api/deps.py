"""Shared API dependencies — the auth gate + session-cookie helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Cookie, HTTPException, Response, WebSocket
from fastapi import status as http_status

from core.config import settings
from services import auth as auth_service

if TYPE_CHECKING:
    from schemas.auth import UserRead

# Read once at import — the cookie name is the dependency's alias.
_COOKIE_NAME = settings.auth.cookie_name


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings.auth.cookie_name,
        value=token,
        httponly=True,
        secure=settings.auth.cookie_secure,
        samesite=settings.auth.cookie_samesite,
        max_age=settings.auth.session_ttl_minutes * 60,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=settings.auth.cookie_name, path="/")


async def get_current_user(
    response: Response,
    session: Annotated[str | None, Cookie(alias=_COOKIE_NAME)] = None,
) -> UserRead:
    if session is None:
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail="not authenticated",
        )
    user = await auth_service.resolve_user(session)
    if user is None:
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail="invalid session",
        )
    # Sliding TTL: re-issue the cookie on every authenticated request.
    set_session_cookie(response, await auth_service.issue_session_token(user.id))
    return user


async def get_websocket_user(websocket: WebSocket) -> UserRead | None:
    """The session's user for a WebSocket handshake; ``None`` = close it unaccepted.

    No sliding re-issue: a handshake has no response to set a cookie on. The Origin
    is checked before this, for every handshake (``OriginProtectionMiddleware``).
    """
    session = websocket.cookies.get(_COOKIE_NAME)
    if not session:
        return None
    return await auth_service.resolve_user(session)
