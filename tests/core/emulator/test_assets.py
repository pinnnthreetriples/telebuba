"""A missing phone file is fetched from its official source and kept only on its hash."""

from __future__ import annotations

import asyncio
import hashlib
from typing import TYPE_CHECKING

import httpx
import pytest
import respx

from core.config import settings
from core.emulator import EmulatorError, assets

if TYPE_CHECKING:
    from pathlib import Path

_URL = "https://github.com/Genymobile/scrcpy/releases/download/v3.3.1/scrcpy-server-v3.3.1"
_BODY = b"scrcpy-server bytes"


@pytest.fixture
def target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "emulator" / "scrcpy-server-v3.3.1"
    monkeypatch.setattr(settings.emulator, "scrcpy_server_path", path)
    monkeypatch.setattr(settings.emulator, "scrcpy_server_version", "3.3.1")
    monkeypatch.setattr(
        settings.emulator, "scrcpy_server_sha256", hashlib.sha256(_BODY).hexdigest()
    )
    monkeypatch.setattr(assets, "_LOCKS", {})
    return path


@pytest.mark.asyncio
async def test_a_missing_file_is_downloaded_from_the_release(target: Path) -> None:
    with respx.mock(assert_all_called=True) as github:
        github.get(_URL).respond(200, content=_BODY)
        assert await assets.ensure_scrcpy_server() == target

    assert target.read_bytes() == _BODY
    assert list(target.parent.iterdir()) == [target], "no scratch file is left behind"


@pytest.mark.asyncio
async def test_a_file_in_place_is_never_fetched_again(target: Path) -> None:
    target.parent.mkdir(parents=True)
    target.write_bytes(b"the operator's own")

    with respx.mock(assert_all_called=False) as github:
        route = github.get(_URL).respond(200, content=_BODY)
        await assets.ensure_scrcpy_server()

    assert not route.called
    assert target.read_bytes() == b"the operator's own"


@pytest.mark.asyncio
async def test_a_wrong_hash_keeps_nothing(target: Path) -> None:
    with respx.mock() as github:
        github.get(_URL).respond(200, content=b"something else")
        with pytest.raises(EmulatorError) as refused:
            await assets.ensure_scrcpy_server()

    assert refused.value.code == "emulator_checksum_mismatch"
    assert list(target.parent.iterdir()) == []


@pytest.mark.parametrize(
    "answer",
    [httpx.Response(404), httpx.ConnectError("offline")],
    ids=["http-error", "offline"],
)
@pytest.mark.asyncio
async def test_a_failed_download_keeps_nothing(
    target: Path, answer: httpx.Response | Exception
) -> None:
    with respx.mock() as github:
        route = github.get(_URL)
        if isinstance(answer, Exception):
            route.side_effect = answer
        else:
            route.return_value = answer
        with pytest.raises(EmulatorError) as refused:
            await assets.ensure_scrcpy_server()

    assert refused.value.code == "emulator_download_failed"
    assert not target.exists()


@pytest.mark.asyncio
async def test_an_oversized_answer_is_cut_off(
    target: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(assets, "_MAX_BYTES", 4)

    with respx.mock() as github:
        github.get(_URL).respond(200, content=_BODY)
        with pytest.raises(EmulatorError) as refused:
            await assets.ensure_scrcpy_server()

    assert refused.value.code == "emulator_download_failed"
    assert list(target.parent.iterdir()) == []


@pytest.mark.asyncio
async def test_a_redirect_off_tls_keeps_nothing(target: Path) -> None:
    with respx.mock(assert_all_called=False) as github:
        github.get(_URL).respond(302, headers={"location": "http://mirror.example/scrcpy"})
        plain = github.get("http://mirror.example/scrcpy").respond(200, content=_BODY)
        with pytest.raises(EmulatorError) as refused:
            await assets.ensure_scrcpy_server()

    assert refused.value.code == "emulator_download_failed", "even the right bytes, off TLS"
    assert not plain.called, "the plain-http hop is never requested"
    assert list(target.parent.iterdir()) == []


@pytest.mark.asyncio
async def test_a_folder_that_cannot_be_made_is_a_failed_download(target: Path) -> None:
    target.parent.parent.mkdir(parents=True, exist_ok=True)
    target.parent.write_bytes(b"a file where the folder goes")

    with respx.mock(assert_all_called=False), pytest.raises(EmulatorError) as refused:
        await assets.ensure_scrcpy_server()

    assert refused.value.code == "emulator_download_failed"


@pytest.mark.usefixtures("target")
@pytest.mark.asyncio
async def test_concurrent_callers_share_one_download() -> None:
    with respx.mock() as github:
        route = github.get(_URL).respond(200, content=_BODY)
        await asyncio.gather(assets.ensure_scrcpy_server(), assets.ensure_scrcpy_server())

    assert route.call_count == 1
