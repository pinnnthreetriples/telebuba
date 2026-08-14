"""External Neurocomment work obeys runtime-generation and account lifecycle fences."""

from __future__ import annotations

import asyncio

import pytest

from schemas.telegram_actions import ActionResult, CommentOnPost
from services.neurocomment import _seams
from services.warming import account_lock


def _comment() -> CommentOnPost:
    return CommentOnPost(channel="@channel", post_id=1, text="hello")


@pytest.mark.asyncio
async def test_revoked_generation_cannot_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    async def _available(_account_id: str) -> bool:
        return True

    async def _execute(*_args: object, **_kwargs: object) -> ActionResult:
        nonlocal called
        called = True
        return ActionResult(status="ok", action_type="comment_on_post", account_id="acc")

    monkeypatch.setattr(_seams, "_account_is_available", _available)
    monkeypatch.setattr(_seams, "_gateway_execute", _execute)

    with (
        _seams.generation_scope(lambda: False),
        pytest.raises(
            _seams.NeurocommentLeaseRevokedError,
        ),
    ):
        await _seams.execute("acc", _comment())

    assert not called


@pytest.mark.asyncio
async def test_generation_revoked_during_dispatch_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = True
    dispatched = asyncio.Event()
    release = asyncio.Event()

    async def _available(_account_id: str) -> bool:
        return True

    async def _execute(*_args: object, **_kwargs: object) -> ActionResult:
        dispatched.set()
        await release.wait()
        return ActionResult(status="ok", action_type="comment_on_post", account_id="acc")

    monkeypatch.setattr(_seams, "_account_is_available", _available)
    monkeypatch.setattr(_seams, "_gateway_execute", _execute)

    async def _run() -> None:
        with _seams.generation_scope(lambda: live):
            await _seams.execute("acc", _comment())

    task = asyncio.create_task(_run())
    await dispatched.wait()
    live = False
    release.set()

    with pytest.raises(_seams.NeurocommentLeaseRevokedError):
        await task


@pytest.mark.asyncio
async def test_account_lifecycle_waits_for_inflight_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatched = asyncio.Event()
    release = asyncio.Event()
    lifecycle_entered = asyncio.Event()

    async def _available(_account_id: str) -> bool:
        return True

    async def _execute(*_args: object, **_kwargs: object) -> ActionResult:
        dispatched.set()
        await release.wait()
        return ActionResult(status="ok", action_type="comment_on_post", account_id="acc")

    monkeypatch.setattr(_seams, "_account_is_available", _available)
    monkeypatch.setattr(_seams, "_gateway_execute", _execute)

    send = asyncio.create_task(_seams.execute("acc", _comment()))
    await dispatched.wait()

    async def _lifecycle() -> None:
        async with account_lock("acc"):
            lifecycle_entered.set()

    lifecycle = asyncio.create_task(_lifecycle())
    await asyncio.sleep(0)
    assert not lifecycle_entered.is_set()
    release.set()
    await send
    await lifecycle
    assert lifecycle_entered.is_set()


@pytest.mark.asyncio
async def test_unavailable_account_is_rejected_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    async def _unavailable(_account_id: str) -> bool:
        return False

    async def _execute(*_args: object, **_kwargs: object) -> ActionResult:
        nonlocal called
        called = True
        return ActionResult(status="ok", action_type="comment_on_post", account_id="acc")

    monkeypatch.setattr(_seams, "_account_is_available", _unavailable)
    monkeypatch.setattr(_seams, "_gateway_execute", _execute)

    with pytest.raises(_seams.NeurocommentAccountUnavailableError):
        await _seams.execute("acc", _comment())

    assert not called


@pytest.mark.asyncio
async def test_comment_requires_completed_neurocomment_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    async def _available(_account_id: str) -> bool:
        return True

    async def _not_handed_off(_account_id: str) -> bool:
        return False

    async def _owns_dispatch() -> bool:
        return True

    async def _execute(*_args: object, **_kwargs: object) -> ActionResult:
        nonlocal called
        called = True
        return ActionResult(status="ok", action_type="comment_on_post", account_id="acc")

    monkeypatch.setattr(_seams, "_account_is_available", _available)
    monkeypatch.setattr(_seams, "_account_is_handed_to_neurocomment", _not_handed_off)
    monkeypatch.setattr(_seams, "_gateway_execute", _execute)

    with pytest.raises(_seams.NeurocommentAccountUnavailableError):
        await _seams.execute_comment("acc", _comment(), _owns_dispatch)

    assert not called
