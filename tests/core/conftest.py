from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from sqlalchemy import create_engine

from core.gemini import close_gemini_client
from core.openai import close_openai_client
from core.spam_signals import set_signal_sink

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator
    from pathlib import Path

    from sqlalchemy.engine import Engine

    _EngineFactory = Callable[[str], Engine]


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


@pytest.fixture
def legacy_engine(tmp_path: Path) -> Iterator[_EngineFactory]:
    """Factory for hand-built legacy DBs, disposed even when the body under test raises.

    A migration that raises used to leak its sqlite connection, and the ResourceWarning
    resurfaced much later as a ``PytestUnraisableExceptionWarning`` blamed on an
    unrelated test in another file. Fixture teardown always runs, so the dispose cannot
    be skipped the way a trailing ``engine.dispose()`` call could.
    """
    engines: list[Engine] = []

    def _make(name: str) -> Engine:
        engine = create_engine(f"sqlite:///{tmp_path / name}", future=True)
        engines.append(engine)
        return engine

    yield _make
    for engine in engines:
        engine.dispose()


@dataclass
class Recorder:
    """A passive-signal receiver that keeps what ``core`` handed it (``core.spam_signals``)."""

    seen: list[tuple[str, str, dict[str, object]]] = field(default_factory=list)

    async def __call__(
        self,
        account_id: str,
        kind: str,
        *,
        source: str,
        operation: str | None = None,
        detail: str | None = None,
    ) -> None:
        self.seen.append((account_id, kind, {"source": source, "op": operation, "detail": detail}))

    def kinds(self) -> list[str]:
        return [kind for _account, kind, _extra in self.seen]


@pytest.fixture
def recorder() -> Recorder:
    """The receiver registered with the port; the suite-wide reset removes it afterwards."""
    rec = Recorder()
    set_signal_sink(rec)
    return rec
