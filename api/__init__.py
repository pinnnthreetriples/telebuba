"""API layer — the UI-thin FastAPI app over ``services/``.

``api/`` may import only ``services``, ``schemas``, ``core.config``,
``core.logging``, and ``fastapi`` (enforced by ``tests/test_architecture.py``).
Routes validate input, call a service, and serialize the result — no business
logic, no direct DB/Telegram access.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.errors import register_error_handlers
from api.v1 import router as v1_router
from core.config import settings

# FastAPI's lifespan: a callable taking the app and yielding once. Typed here in
# stdlib terms so api/ needs no starlette import (allowlist discipline).
Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]


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
    register_error_handlers(app)
    app.include_router(v1_router, prefix=f"/api/{settings.api.version}")
    return app


__all__ = ["create_app"]
