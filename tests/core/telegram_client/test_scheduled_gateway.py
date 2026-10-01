"""Gateway behaviour the scheduled publisher depends on.

- A flood on an automated (domain) call never writes the sticky ``flood_wait``
  status: bulk scheduling must not park a slice of the fleet out of warming.
- Story quota refusals, which Telethon has no class for, get stable codes.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image
from telethon import errors

from core.telegram_client import execute
from schemas.telegram_actions import PostStory, SetProfilePhoto
from tests.core.telegram_client.helpers import patch_action_client


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (640, 640), "red").save(buffer, format="PNG")
    return buffer.getvalue()


class _RefusingClient:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    async def connect(self) -> None:
        return None

    async def get_input_entity(self, _peer: object) -> object:
        return object()

    async def upload_file(self, *_args: object, **_kwargs: object) -> object:
        return object()

    async def __call__(self, _request: object) -> object:
        raise self.exc


@pytest.fixture
def marked(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    async def _mark(account_id: str, status: str) -> None:
        calls.append((account_id, status))

    monkeypatch.setattr("core.telegram_client._actions._mark_account_status", _mark)
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("domain", "expected"), [(None, [("acc", "flood_wait")]), ("scheduled", [])]
)
async def test_only_the_operators_own_edit_marks_the_account_flood_wait(
    monkeypatch: pytest.MonkeyPatch,
    marked: list[tuple[str, str]],
    domain: str | None,
    expected: list[tuple[str, str]],
) -> None:
    patch_action_client(
        monkeypatch, _RefusingClient(errors.FloodWaitError(request=None, capture=30))
    )

    result = await execute("acc", SetProfilePhoto(filename="p.png", content=_png()), domain=domain)

    assert (result.status, result.flood_wait_seconds) == ("flood_wait", 30)
    assert marked == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "code"),
    [("STORIES_TOO_MUCH", "stories_too_much"), ("STORY_SEND_FLOOD_WEEKLY_3", "story_send_flood")],
)
async def test_story_quota_refusals_get_stable_codes(
    monkeypatch: pytest.MonkeyPatch,
    marked: list[tuple[str, str]],
    message: str,
    code: str,
) -> None:
    refusal = errors.RPCError(request=None, message=message, code=400)
    patch_action_client(monkeypatch, _RefusingClient(refusal))

    result = await execute(
        "acc",
        PostStory(filename="s.png", content=_png(), media_kind="image"),
        domain="scheduled",
    )

    assert (result.status, result.error_type, result.error_message) == (
        "failed",
        "ProfileGatewayError",
        code,
    )
    assert marked == []
