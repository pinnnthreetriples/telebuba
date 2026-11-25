"""One ``create_app()`` build shared by the API tests of a process.

``create_app()`` rebuilds every route — about half a second, which was nearly all of an
API test's cost. The route table depends only on the code and on the settings in
``wiring_key()``, so one build serves every test with the same key. Everything on that
app a test or a request can change is put back by ``shared_app()`` before each use.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastapi.datastructures import State

from api import create_app
from core.config import settings

if TYPE_CHECKING:
    from fastapi import FastAPI
    from starlette.middleware import Middleware
    from starlette.routing import BaseRoute


@dataclass(frozen=True, slots=True)
class _BuiltApp:
    """One ``create_app()`` result plus the wiring a test could have changed on it."""

    application: FastAPI
    routes: tuple[BaseRoute, ...]
    middleware: tuple[Middleware, ...]
    exception_handlers: dict[Any, Any]


_BUILT: dict[str, _BuiltApp] = {}


def wiring_key() -> str:
    """Every setting ``create_app`` freezes into the app it returns.

    ``tests/api/test_shared_app.py`` fails when ``create_app`` starts reading one that
    is missing here.
    """
    return json.dumps(
        [
            settings.api.model_dump(mode="json"),
            settings.auth.cookie_name,
            settings.chats.media_max_bytes,
        ],
        sort_keys=True,
    )


def shared_app() -> FastAPI:
    """Return the app built for the current settings, as ``create_app()`` left it."""
    key = wiring_key()
    built = _BUILT.get(key)
    if built is None:
        application = create_app()
        built = _BUILT[key] = _BuiltApp(
            application=application,
            routes=tuple(application.router.routes),
            middleware=tuple(application.user_middleware),
            exception_handlers=dict(application.exception_handlers),
        )
    application = built.application
    application.router.routes[:] = built.routes
    application.user_middleware[:] = built.middleware
    application.exception_handlers.clear()
    application.exception_handlers.update(built.exception_handlers)
    # Rebuilt on the next request: the body limiter's upload gate is an asyncio
    # primitive and must not carry waiters or a loop from the previous test.
    application.middleware_stack = None
    application.openapi_schema = None
    application.state = State()
    application.dependency_overrides.clear()
    return application
