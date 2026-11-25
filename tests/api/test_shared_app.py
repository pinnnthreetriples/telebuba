"""The shared test app: nothing a test does to it reaches the next test."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from api import create_app
from api._middleware import SecurityHeadersMiddleware
from api.deps import get_current_user
from core.config import settings
from tests.api._shared_app import shared_app, wiring_key

if TYPE_CHECKING:
    import pytest


class _Reads:
    """Stands in for ``settings`` and notes every value read through it."""

    def __init__(self, target: object, path: tuple[str, ...], seen: set[tuple[str, ...]]) -> None:
        self._target = target
        self._path = path
        self._seen = seen

    def __getattr__(self, name: str) -> object:
        value = getattr(self._target, name)
        path = (*self._path, name)
        if isinstance(value, BaseModel):
            return _Reads(value, path, self._seen)
        self._seen.add(path)
        return value


def _changed(value: object) -> object:
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    if isinstance(value, str):
        return value + "x"
    if isinstance(value, list):
        return [*value, "x"]
    message = f"teach _changed about {type(value).__name__}"
    raise AssertionError(message)


def test_every_setting_create_app_reads_is_part_of_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: set[tuple[str, ...]] = set()
    monkeypatch.setattr("api.settings", _Reads(settings, (), seen))
    create_app()
    assert ("api", "version") in seen  # the recorder really sat in create_app's path

    for path in sorted(seen):
        owner: object = settings
        for name in path[:-1]:
            owner = getattr(owner, name)
        before = wiring_key()
        with monkeypatch.context() as patch:
            patch.setattr(owner, path[-1], _changed(getattr(owner, path[-1])))
            assert wiring_key() != before, ".".join(path)


def test_a_changed_wiring_setting_gets_its_own_app(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.api, "docs_enabled", False)
    closed = shared_app()
    monkeypatch.setattr(settings.api, "docs_enabled", True)
    opened = shared_app()

    assert opened is not closed
    assert opened.docs_url == "/docs"
    assert closed.docs_url is None


def test_what_a_test_leaves_on_the_app_is_gone_for_the_next_one() -> None:
    pristine = shared_app()
    routes = list(pristine.router.routes)
    middleware = list(pristine.user_middleware)
    handlers = dict(pristine.exception_handlers)

    pristine.dependency_overrides[get_current_user] = lambda: None
    pristine.add_api_route("/left-behind", lambda: None)
    pristine.add_middleware(SecurityHeadersMiddleware)
    pristine.add_exception_handler(KeyError, next(iter(handlers.values())))
    pristine.state.left_behind = True
    pristine.openapi()
    pristine.middleware_stack = pristine.build_middleware_stack()

    again = shared_app()

    assert again is pristine
    assert again.dependency_overrides == {}
    assert list(again.router.routes) == routes
    assert list(again.user_middleware) == middleware
    assert dict(again.exception_handlers) == handlers
    assert not hasattr(again.state, "left_behind")
    assert again.openapi_schema is None
    assert again.middleware_stack is None
