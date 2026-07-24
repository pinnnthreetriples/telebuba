"""Tests for ``core.telegram_client`` push listener — ``subscribe_posts`` et al.

Mirrors ``test_telegram_read``: patches ``get_client`` / ``fetch_account`` on the
owning submodule and drives a fake client. The fake captures the registered
``(callback, event_filter)`` so the test can synthesise a Telethon-shaped event
and invoke the handler directly — no live MTProto loop needed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar
from unittest.mock import MagicMock

import pytest

from core.config import settings
from core.db import configure_database
from core.logging import reset_logging_for_tests, setup_logging
from core.telegram_client import _listener as listener_mod
from core.telegram_client import (
    stop_post_listener,
    subscribe_posts,
    update_post_subscription,
)
from schemas.telegram_actions import NewPostEvent

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.telegram, "session_dir", tmp_path / "sessions")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    reset_logging_for_tests()
    setup_logging()
    listener_mod._reset_for_tests()
    listener_mod._CLIENTS.clear()
    yield
    listener_mod._reset_for_tests()
    listener_mod._CLIENTS.clear()


class FakeClient:
    """Captures handler registration and resolves channels to deterministic ids.

    ``get_peer_id`` returns ``hash(channel)``-free ids so the reverse map is
    predictable: ``@news`` -> -100, ``@deals`` -> -200.
    """

    PEER_IDS: ClassVar[dict[str, int]] = {"@news": -100, "@deals": -200}

    def __init__(self) -> None:
        # Callback typed as an async callable so invoking it in tests type-checks.
        self.handlers: list[tuple[Callable[[Any], Awaitable[None]], object]] = []
        self.removed: list[tuple[object, object]] = []
        self.catch_up_called = False
        # Every channel string passed to get_peer_id, so a test can prove a
        # channel is resolved via RPC at most once and then served from cache.
        self.peer_id_calls: list[str] = []

    async def get_peer_id(self, channel: str) -> int:
        self.peer_id_calls.append(channel)
        return self.PEER_IDS[channel]

    def add_event_handler(
        self,
        callback: Callable[[Any], Awaitable[None]],
        event_filter: object,
    ) -> None:
        self.handlers.append((callback, event_filter))

    def remove_event_handler(self, callback: object, event_filter: object) -> int:
        self.removed.append((callback, event_filter))
        self.handlers = [h for h in self.handlers if h[0] is not callback]
        return 1

    async def catch_up(self) -> None:  # pragma: no cover - must never be called
        self.catch_up_called = True


def _patch_client(monkeypatch: pytest.MonkeyPatch, client: object) -> None:
    """Fake ``get_client`` and mirror production's pool caching.

    Production ``get_client`` stores the connected client in the pool's
    ``_CLIENTS`` map; ``stop_post_listener`` now peeks that map instead of
    forcing a fresh connect, so the fake caches the client the same way.
    """

    async def fake_get_client(account_id: str) -> object:
        listener_mod._CLIENTS[account_id] = client  # ty: ignore[invalid-assignment]
        return client

    monkeypatch.setattr("core.telegram_client._listener.get_client", fake_get_client)


def _make_event(  # noqa: PLR0913 - test helper mirrors the Telethon message fields
    *,
    chat_id: int,
    post_id: int,
    text: str | None,
    media: object,
    post: object,
    fwd_from: object = None,
) -> object:
    message = MagicMock(id=post_id, message=text, media=media, post=post, fwd_from=fwd_from)
    return MagicMock(message=message, chat_id=chat_id)


@pytest.mark.asyncio
async def test_subscribe_posts_surfaces_new_broadcast_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient()
    _patch_client(monkeypatch, client)
    received: list[NewPostEvent] = []

    async def on_post(event: NewPostEvent) -> None:
        received.append(event)

    await subscribe_posts("listener-1", ["@news", "@deals"], on_post)

    assert len(client.handlers) == 1
    callback, _event_filter = client.handlers[0]

    await callback(
        _make_event(chat_id=-200, post_id=42, text="big sale", media=object(), post=True),
    )

    assert received == [
        NewPostEvent(channel="@deals", post_id=42, text="big sale", has_media=True),
    ]


@pytest.mark.asyncio
async def test_non_broadcast_event_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient()
    _patch_client(monkeypatch, client)
    received: list[NewPostEvent] = []

    async def on_post(event: NewPostEvent) -> None:
        received.append(event)

    await subscribe_posts("listener-2", ["@news"], on_post)
    callback, _ = client.handlers[0]

    # Megagroup message (post falsy) must not surface.
    await callback(
        _make_event(chat_id=-100, post_id=7, text="chatter", media=None, post=False),
    )

    assert received == []


@pytest.mark.asyncio
async def test_post_without_text_or_media_normalises(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient()
    _patch_client(monkeypatch, client)
    received: list[NewPostEvent] = []

    async def on_post(event: NewPostEvent) -> None:
        received.append(event)

    await subscribe_posts("listener-3", ["@news"], on_post)
    callback, _ = client.handlers[0]

    await callback(
        _make_event(chat_id=-100, post_id=9, text=None, media=None, post=True),
    )

    assert received == [NewPostEvent(channel="@news", post_id=9, text="", has_media=False)]


@pytest.mark.asyncio
async def test_unknown_chat_id_falls_back_to_str(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient()
    _patch_client(monkeypatch, client)
    received: list[NewPostEvent] = []

    async def on_post(event: NewPostEvent) -> None:
        received.append(event)

    await subscribe_posts("listener-4", ["@news"], on_post)
    callback, _ = client.handlers[0]

    await callback(
        _make_event(chat_id=-999, post_id=1, text="x", media=None, post=True),
    )

    assert received[0].channel == "-999"


@pytest.mark.asyncio
async def test_update_post_subscription_swaps_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient()
    _patch_client(monkeypatch, client)

    async def on_post(_event: NewPostEvent) -> None:
        return None

    await subscribe_posts("listener-5", ["@news"], on_post)
    first_callback, _ = client.handlers[0]

    await update_post_subscription("listener-5", ["@deals"], on_post)

    # Old handler removed, exactly one live handler remains.
    assert any(removed[0] is first_callback for removed in client.removed)
    assert len(client.handlers) == 1


@pytest.mark.asyncio
async def test_resubscribe_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient()
    _patch_client(monkeypatch, client)

    async def on_post(_event: NewPostEvent) -> None:
        return None

    await subscribe_posts("listener-6", ["@news"], on_post)
    await subscribe_posts("listener-6", ["@news"], on_post)

    # Re-subscribing removes the prior handler first — never two live handlers.
    assert len(client.handlers) == 1


@pytest.mark.asyncio
async def test_stop_post_listener_removes_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient()
    _patch_client(monkeypatch, client)

    async def on_post(_event: NewPostEvent) -> None:
        return None

    await subscribe_posts("listener-7", ["@news"], on_post)
    await stop_post_listener("listener-7")

    assert client.handlers == []


@pytest.mark.asyncio
async def test_stop_post_listener_no_op_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient()
    _patch_client(monkeypatch, client)

    # No prior subscribe — must not raise.
    await stop_post_listener("listener-never")

    assert client.removed == []


@pytest.mark.asyncio
async def test_stop_post_listener_without_cached_client_does_not_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stopping a listener whose client is gone must NOT force a fresh connect.

    Regression: the old stop path called ``get_client`` just to detach the
    handler, so stopping a broken listener (or stopping during pool shutdown)
    would connect a throwaway client or raise.
    """
    client = FakeClient()
    _patch_client(monkeypatch, client)

    async def on_post(_event: NewPostEvent) -> None:
        return None

    await subscribe_posts("listener-broken", ["@news"], on_post)
    # Simulate the pooled client being gone (crash / shutdown): drop it from the
    # pool cache, leaving only the registry entry.
    listener_mod._CLIENTS.clear()

    get_client_calls = {"n": 0}

    async def exploding_get_client(_account_id: str) -> object:
        get_client_calls["n"] += 1
        msg = "get_client must not be called by stop_post_listener"
        raise AssertionError(msg)

    monkeypatch.setattr("core.telegram_client._listener.get_client", exploding_get_client)

    # Must neither call get_client nor raise.
    await stop_post_listener("listener-broken")

    assert get_client_calls["n"] == 0
    assert "listener-broken" not in listener_mod._HANDLERS


@pytest.mark.asyncio
async def test_pool_rebuild_reattaches_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    """A rebuilt client (fresh, handler-less) must regain the NewMessage handler.

    Drives the rebuild hook the listener registers into the pool: after
    ``subscribe_posts`` the handler lives on the original client; when the pool
    hands a fresh client to the hook, the same handler must be re-added.
    """
    client = FakeClient()
    _patch_client(monkeypatch, client)

    async def on_post(_event: NewPostEvent) -> None:
        return None

    await subscribe_posts("listener-rebuild", ["@news"], on_post)
    original_handler, original_filter = client.handlers[0]

    rebuilt = FakeClient()
    await listener_mod._reattach_on_rebuild("listener-rebuild", rebuilt)  # ty: ignore[invalid-argument-type]

    assert len(rebuilt.handlers) == 1, "the rebuilt client must regain the handler"
    reattached_handler, reattached_filter = rebuilt.handlers[0]
    assert reattached_handler is original_handler
    assert reattached_filter is original_filter


@pytest.mark.asyncio
async def test_pool_rebuild_hook_noop_after_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    """After stop_post_listener a rebuild must not resurrect the dropped handler."""
    client = FakeClient()
    _patch_client(monkeypatch, client)

    async def on_post(_event: NewPostEvent) -> None:
        return None

    await subscribe_posts("listener-gone", ["@news"], on_post)
    await stop_post_listener("listener-gone")

    rebuilt = FakeClient()
    await listener_mod._reattach_on_rebuild("listener-gone", rebuilt)  # ty: ignore[invalid-argument-type]

    assert rebuilt.handlers == [], "a stopped listener must not re-attach on rebuild"


@pytest.mark.asyncio
async def test_no_backfill_catch_up_never_called(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient()
    _patch_client(monkeypatch, client)

    async def on_post(_event: NewPostEvent) -> None:
        return None

    await subscribe_posts("listener-8", ["@news"], on_post)
    callback, _ = client.handlers[0]
    await callback(_make_event(chat_id=-100, post_id=1, text="x", media=None, post=True))

    assert client.catch_up_called is False


@pytest.mark.asyncio
async def test_forwarded_post_sets_is_forward(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient()
    _patch_client(monkeypatch, client)
    received: list[NewPostEvent] = []

    async def on_post(event: NewPostEvent) -> None:
        received.append(event)

    await subscribe_posts("listener-fwd", ["@news"], on_post)
    callback, _ = client.handlers[0]

    # A forwarded/reposted broadcast (fwd_from present) must be flagged so the
    # engine can drop it — this is the field the engine's forward filter reads.
    await callback(
        _make_event(
            chat_id=-100, post_id=5, text="reposted", media=None, post=True, fwd_from=object()
        ),
    )

    assert received[0].is_forward is True


@pytest.mark.asyncio
async def test_unresolvable_channel_is_skipped_others_still_subscribe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient()
    _patch_client(monkeypatch, client)
    received: list[NewPostEvent] = []

    async def on_post(event: NewPostEvent) -> None:
        received.append(event)

    # "@gone" is absent from PEER_IDS -> get_peer_id raises; "@news" resolves.
    await subscribe_posts("listener-bad", ["@gone", "@news"], on_post)

    # One bad channel must NOT abort the batch — the good channel still listens.
    assert len(client.handlers) == 1
    callback, _ = client.handlers[0]
    await callback(_make_event(chat_id=-100, post_id=1, text="x", media=None, post=True))
    assert received[0].channel == "@news"


@pytest.mark.asyncio
async def test_all_unresolvable_registers_no_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient()
    _patch_client(monkeypatch, client)

    async def on_post(_event: NewPostEvent) -> None:
        return None

    # Every channel fails to resolve -> register nothing: events.NewMessage(chats=[])
    # would otherwise watch EVERY chat.
    await subscribe_posts("listener-allbad", ["@gone", "@missing"], on_post)

    assert client.handlers == []


@pytest.mark.asyncio
async def test_peer_id_resolved_once_then_served_from_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A channel is resolved via RPC once; a second reconcile reuses the cache.

    Reconcile fires on every channel link/unlink and on boot, so re-resolving
    every watched channel each time is O(channels) serial RPCs. Peer ids are
    stable, so the second subscribe must issue no fresh get_peer_id calls.
    """
    client = FakeClient()
    _patch_client(monkeypatch, client)
    received: list[NewPostEvent] = []

    async def on_post(event: NewPostEvent) -> None:
        received.append(event)

    await subscribe_posts("listener-cache", ["@news", "@deals"], on_post)
    assert sorted(client.peer_id_calls) == ["@deals", "@news"]

    # Second reconcile with the same channels: no new RPCs, cache serves the ids.
    await subscribe_posts("listener-cache", ["@news", "@deals"], on_post)
    assert sorted(client.peer_id_calls) == ["@deals", "@news"]

    # Behaviour unchanged: the filtered handler still maps peer id -> channel.
    callback, _ = client.handlers[0]
    await callback(_make_event(chat_id=-200, post_id=1, text="x", media=None, post=True))
    assert received[0].channel == "@deals"


@pytest.mark.asyncio
async def test_failed_resolution_is_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """A channel that fails to resolve is retried next reconcile, not cached."""
    client = FakeClient()
    _patch_client(monkeypatch, client)

    async def on_post(_event: NewPostEvent) -> None:
        return None

    # "@gone" is absent from PEER_IDS -> get_peer_id raises and must NOT be cached.
    await subscribe_posts("listener-retry", ["@gone"], on_post)
    assert client.peer_id_calls == ["@gone"]

    # Second reconcile must re-attempt the unresolved channel (no poisoned cache).
    await subscribe_posts("listener-retry", ["@gone"], on_post)
    assert client.peer_id_calls == ["@gone", "@gone"]


@pytest.mark.asyncio
async def test_callback_error_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient()
    _patch_client(monkeypatch, client)

    async def boom(_event: NewPostEvent) -> None:
        msg = "callback exploded"
        raise RuntimeError(msg)

    await subscribe_posts("listener-9", ["@news"], boom)
    callback, _ = client.handlers[0]

    # A raising callback must NOT propagate out of the handler (would kill the loop).
    await callback(_make_event(chat_id=-100, post_id=1, text="x", media=None, post=True))
