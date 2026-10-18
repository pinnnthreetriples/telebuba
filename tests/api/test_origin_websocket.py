"""Origin protection for WebSocket handshakes: every one needs a trusted Origin."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from api._middleware import OriginProtectionMiddleware

if TYPE_CHECKING:
    from api._middleware import Message, Scope


def _scope(scheme: str, origin: str | None, *, cookie: bool) -> Scope:
    headers = [(b"host", b"farm.local:8080")]
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    if cookie:
        headers.append((b"cookie", b"tb_session=token"))
    return {"type": "websocket", "scheme": scheme, "path": "/ws", "headers": headers}


async def _run(scope: Scope, *, allowed: tuple[str, ...] = ()) -> tuple[bool, list[Message]]:
    reached: list[bool] = []
    sent: list[Message] = []

    async def app(_scope: Scope, _receive: Any, _send: Any) -> None:
        reached.append(True)

    async def receive() -> Message:
        return {"type": "websocket.connect"}

    async def send(message: Message) -> None:
        sent.append(message)

    middleware = OriginProtectionMiddleware(app, cookie_name="tb_session", allowed_origins=allowed)
    await middleware(scope, receive, send)
    return bool(reached), sent


@pytest.mark.parametrize(
    ("scheme", "origin"),
    [("ws", "http://farm.local:8080"), ("wss", "https://farm.local:8080")],
)
@pytest.mark.asyncio
async def test_the_page_own_origin_opens_the_socket(scheme: str, origin: str) -> None:
    reached, sent = await _run(_scope(scheme, origin, cookie=True))

    assert reached
    assert sent == []


@pytest.mark.parametrize(
    "origin",
    [None, "https://farm.local:8080", "http://evil.example", "null"],
)
@pytest.mark.parametrize("cookie", ["with_cookie", "without_cookie"])
@pytest.mark.asyncio
async def test_any_other_origin_is_closed_before_accept(origin: str | None, cookie: str) -> None:
    reached, sent = await _run(_scope("ws", origin, cookie=cookie == "with_cookie"))

    assert not reached
    assert sent == [{"type": "websocket.close", "code": 1008}]


@pytest.mark.asyncio
async def test_a_configured_spa_origin_is_trusted() -> None:
    reached, _sent = await _run(
        _scope("ws", "http://localhost:5173", cookie=True), allowed=("http://localhost:5173",)
    )

    assert reached
