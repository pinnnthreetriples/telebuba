"""Auth endpoints — login (sets the session cookie), logout, me."""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi import status as http_status

from api.deps import clear_session_cookie, get_current_user, set_session_cookie
from core.config import settings
from schemas.auth import LoginRequest, UserRead
from services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=UserRead, operation_id="login")
async def login(body: LoginRequest, request: Request, response: Response) -> UserRead:
    if not settings.auth.secret:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="authentication is not configured",
        )
    client_key = request.client.host if request.client else "unknown"
    if not auth_service.check_login_rate_limit(client_key, time.time()):
        raise HTTPException(
            status_code=http_status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many login attempts",
        )
    user = await auth_service.authenticate(body)
    if user is None:
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
        )
    set_session_cookie(response, auth_service.issue_session_token(user.id))
    return user


@router.post("/logout", status_code=http_status.HTTP_204_NO_CONTENT, operation_id="logout")
async def logout(response: Response) -> None:
    clear_session_cookie(response)


@router.get("/me", response_model=UserRead, operation_id="getMe")
async def me(user: Annotated[UserRead, Depends(get_current_user)]) -> UserRead:
    return user
