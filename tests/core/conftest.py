from __future__ import annotations

from typing import TYPE_CHECKING

import pytest_asyncio

from core.gemini import close_gemini_client
from core.openai import close_openai_client

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import pytest


_HTTP_PROXY_ENV_VARS = (
    "ALL_PROXY",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "all_proxy",
    "https_proxy",
    "http_proxy",
)


def _clear_http_proxy_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep mocked HTTP gateway tests independent from the host environment."""
    for name in _HTTP_PROXY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest_asyncio.fixture
async def isolated_gemini_client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    _clear_http_proxy_environment(monkeypatch)
    yield
    await close_gemini_client()


@pytest_asyncio.fixture
async def isolated_openai_client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    _clear_http_proxy_environment(monkeypatch)
    yield
    await close_openai_client()
