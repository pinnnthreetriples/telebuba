"""API layer — the UI-thin FastAPI app over ``services/``.

``api/`` may import only ``services``, ``schemas``, ``core.config``,
``core.logging``, and ``fastapi`` (enforced by ``tests/test_architecture.py``).
Routes validate input, call a service, and serialize the result — no business
logic, no direct DB/Telegram access.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api._middleware import (
    BodyLimitPolicy,
    BodySizeLimitMiddleware,
    OriginProtectionMiddleware,
    SecurityHeadersMiddleware,
)
from api.errors import register_error_handlers
from api.v1 import router as v1_router
from core.config import settings
from services import auth as auth_service

# FastAPI's lifespan: a callable taking the app and yielding once. Typed here in
# stdlib terms so api/ needs no starlette import (allowlist discipline).
Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]


async def _valid_session(token: str) -> bool:
    """Fail closed unless JWT signature, expiry, user, and revocation version pass."""
    return await auth_service.resolve_user(token) is not None


def _large_upload_patterns() -> tuple[str, ...]:
    """Exact upload endpoints allowed to exceed the anonymous body budget."""
    prefix = re.escape(f"/api/{settings.api.version}")
    segment = r"[^/]+"
    return (
        rf"{prefix}/accounts/import-(?:tdata|session)",
        rf"{prefix}/accounts/photo",
        rf"{prefix}/accounts/{segment}/(?:story|music)",
        rf"{prefix}/accounts/{segment}/channels/{segment}/(?:photo|posts)",
    )


def create_app(lifespan: Lifespan | None = None) -> FastAPI:
    """Build the FastAPI app: CORS, error envelope, and the ``/api/v1`` router.

    Runtime startup/shutdown (warming + neurocomment) and static frontend serving
    are the composition root's job (``main.py``); they are injected via ``lifespan``.
    """
    app = FastAPI(title="Telebuba API", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.api.cors_origins,
        allow_credentials=settings.api.cors_allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Added last = outermost (``add_middleware`` prepends, the stack builds in
    # reverse), which is what both need: the byte counter must reject before
    # routing resolves the auth dependency, and the header stamp must wrap its 413.
    app.add_middleware(
        BodySizeLimitMiddleware,
        policy=BodyLimitPolicy(
            max_bytes=settings.api.max_request_bytes,
            max_anonymous_bytes=settings.api.max_anonymous_request_bytes,
            cookie_name=settings.auth.cookie_name,
            max_concurrent_uploads=settings.api.max_concurrent_uploads,
            large_upload_path_patterns=_large_upload_patterns(),
        ),
        validate_session=_valid_session,
    )
    # Added after the body limiter, therefore wraps it: a browser-originated CSRF
    # refusal happens before session DB validation or any request-body read.
    app.add_middleware(
        OriginProtectionMiddleware,
        cookie_name=settings.auth.cookie_name,
        allowed_origins=settings.api.cors_origins,
    )
    app.add_middleware(SecurityHeadersMiddleware)
    register_error_handlers(app)
    app.include_router(v1_router, prefix=f"/api/{settings.api.version}")
    return app


__all__ = ["create_app"]
