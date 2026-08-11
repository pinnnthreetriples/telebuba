"""Transport-level upload staging: streaming bounds and credential cleanup."""

from __future__ import annotations

import asyncio
import os
import stat
import time
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile

from api.v1._uploads import cleanup_stale_uploads, staged_upload
from core.config import settings


@pytest.fixture(autouse=True)
def _private_staging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.api, "upload_staging_dir", tmp_path / "uploads")
    monkeypatch.setattr(settings.api, "upload_staging_ttl_seconds", 60)


@pytest.mark.asyncio
async def test_staged_upload_removes_private_file_after_cancellation() -> None:
    upload = UploadFile(file=BytesIO(b"credential-data"), filename="tdata.zip", size=15)
    staged = None

    async def _cancel_during_use() -> None:
        nonlocal staged
        async with staged_upload(upload, max_bytes=100, detail="too large") as path:
            staged = path
            assert path.read_bytes() == b"credential-data"
            if os.name == "posix":
                assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
                assert stat.S_IMODE(path.stat().st_mode) == 0o600
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await _cancel_during_use()

    assert staged is not None
    assert not staged.exists()


@pytest.mark.asyncio
async def test_staged_upload_enforces_actual_stream_size_when_metadata_is_missing() -> None:
    upload = UploadFile(file=BytesIO(b"oversized"), filename="tdata.zip", size=None)

    with pytest.raises(HTTPException) as caught:
        async with staged_upload(upload, max_bytes=2, detail="too large"):
            pass

    assert caught.value.status_code == 400
    assert caught.value.detail == "too large"


@pytest.mark.asyncio
async def test_staged_upload_retries_transient_unlink_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_unlink = Path.unlink
    attempts = 0

    def _flaky_unlink(path: Path, *, missing_ok: bool = False) -> None:
        nonlocal attempts
        if path.name.startswith("telebuba_upload_"):
            attempts += 1
            if attempts < 3:
                message = "transient"
                raise OSError(message)
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", _flaky_unlink)
    upload = UploadFile(file=BytesIO(b"credential-data"), filename="a.session", size=15)
    staged = None
    async with staged_upload(upload, max_bytes=100, detail="too large") as path:
        staged = path

    assert attempts == 3
    assert staged is not None
    assert not staged.exists()


@pytest.mark.asyncio
async def test_persistent_cleanup_failure_is_logged_without_absolute_path(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_unlink = Path.unlink

    def _failed_unlink(path: Path, *, missing_ok: bool = False) -> None:
        del path, missing_ok
        message = "persistent"
        raise OSError(message)

    monkeypatch.setattr(Path, "unlink", _failed_unlink)
    upload = UploadFile(file=BytesIO(b"credential-data"), filename="a.session", size=15)
    staged = None
    async with staged_upload(upload, max_bytes=100, detail="too large") as path:
        staged = path

    assert staged is not None
    assert staged.exists()
    assert "upload staging cleanup failed (OSError)" in caplog.text
    assert str(settings.api.upload_staging_dir.resolve()) not in caplog.text
    original_unlink(staged)


@pytest.mark.asyncio
async def test_startup_cleanup_removes_only_stale_staging_files() -> None:
    directory = settings.api.upload_staging_dir
    directory.mkdir(parents=True)
    stale = directory / "telebuba_upload_stale"
    fresh = directory / "telebuba_upload_fresh"
    unrelated = directory / "operator-note"
    for path in (stale, fresh, unrelated):
        path.write_bytes(b"x")
    old = time.time() - settings.api.upload_staging_ttl_seconds - 1
    os.utime(stale, (old, old))

    await cleanup_stale_uploads()

    assert not stale.exists()
    assert fresh.exists()
    assert unrelated.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits only")
@pytest.mark.asyncio
async def test_staging_refuses_an_existing_world_readable_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = settings.api.upload_staging_dir
    directory.mkdir(parents=True)
    directory.chmod(0o755)  # intentionally construct an insecure fixture
    monkeypatch.setattr("api.v1._uploads._make_private_dir", lambda _path: None)

    with pytest.raises(RuntimeError, match="must be owner-only") as caught:
        await cleanup_stale_uploads()

    assert str(directory) not in str(caught.value)


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits only")
@pytest.mark.asyncio
async def test_staging_fails_closed_when_permission_repair_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = settings.api.upload_staging_dir
    directory.mkdir(parents=True)
    directory.chmod(0o755)  # intentionally construct an insecure fixture

    def _failed_chmod(_path: Path, _mode: int) -> None:
        message = "permission repair failed"
        raise OSError(message)

    monkeypatch.setattr(Path, "chmod", _failed_chmod)
    with pytest.raises(RuntimeError, match="permissions could not be set") as caught:
        await cleanup_stale_uploads()

    assert str(directory) not in str(caught.value)
