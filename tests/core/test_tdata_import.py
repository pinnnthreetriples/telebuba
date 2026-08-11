"""Tests for ``core.tdata_import`` — safe zip extraction + opentele2 wrapper.

The real opentele2 conversion can't run inside the test harness (it needs a real
tdata payload, a real Telegram authorisation, and network). What we DO test for
real:

- zip security validators reject path traversal, absolute paths, too many files,
  zip bombs, POSIX symlinks, and invalid zips with the right status.
- happy path returns ``ok`` and writes a session file (opentele2 mocked).
- failures from opentele2 surface as ``conversion_error``, carry the partial summary
  built so far, and report the exception's CLASS NAME — never its text.
- the private temp directory is cleaned up on every code path.
"""

from __future__ import annotations

import io
import zipfile
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.tdata_import import convert_tdata_zip
from schemas.tdata import TdataConvertRequest

if TYPE_CHECKING:
    from pathlib import Path


def _zip(entries: dict[str, bytes]) -> bytes:
    """Build an in-memory zip from name → content pairs."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _zip_with_symlink(link_name: str, target: str) -> bytes:
    """Build a zip that contains a POSIX symlink entry."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        info = zipfile.ZipInfo(filename=link_name)
        info.create_system = 3  # POSIX
        info.external_attr = (0o120777 << 16) | 0x10  # symlink type
        zf.writestr(info, target)
    return buf.getvalue()


@pytest.fixture
def sessions_dir(tmp_path: Path) -> Path:
    d = tmp_path / "sessions"
    d.mkdir()
    return d


@pytest.fixture
def tmp_base(tmp_path: Path) -> Path:
    d = tmp_path / "tmpbase"
    d.mkdir()
    return d


@pytest.mark.asyncio
async def test_rejects_invalid_zip(sessions_dir: Path, tmp_base: Path) -> None:
    req = TdataConvertRequest(filename="bad.zip", content=b"NOT-A-ZIP")
    result = await convert_tdata_zip(req, sessions_dir, tmp_base=tmp_base)
    assert result.status == "invalid_zip"
    assert list(tmp_base.iterdir()) == []


@pytest.mark.asyncio
async def test_rejects_path_traversal(sessions_dir: Path, tmp_base: Path) -> None:
    payload = _zip({"../etc/passwd": b"evil"})
    req = TdataConvertRequest(filename="bad.zip", content=payload)
    result = await convert_tdata_zip(req, sessions_dir, tmp_base=tmp_base)
    assert result.status == "unsafe_path"
    assert list(tmp_base.iterdir()) == []


@pytest.mark.asyncio
async def test_rejects_absolute_path(sessions_dir: Path, tmp_base: Path) -> None:
    payload = _zip({"/etc/passwd": b"evil"})
    req = TdataConvertRequest(filename="bad.zip", content=payload)
    result = await convert_tdata_zip(req, sessions_dir, tmp_base=tmp_base)
    assert result.status == "unsafe_path"
    assert list(tmp_base.iterdir()) == []


@pytest.mark.asyncio
async def test_rejects_symlink(sessions_dir: Path, tmp_base: Path) -> None:
    payload = _zip_with_symlink("link", "/etc/passwd")
    req = TdataConvertRequest(filename="bad.zip", content=payload)
    result = await convert_tdata_zip(req, sessions_dir, tmp_base=tmp_base)
    assert result.status == "symlinks_not_allowed"
    assert list(tmp_base.iterdir()) == []


@pytest.mark.asyncio
async def test_rejects_too_many_files(sessions_dir: Path, tmp_base: Path) -> None:
    payload = _zip({"a": b"", "b": b"", "c": b""})
    req = TdataConvertRequest(filename="bad.zip", content=payload)
    with patch("core.tdata_import.MAX_FILE_COUNT", 2):
        result = await convert_tdata_zip(req, sessions_dir, tmp_base=tmp_base)
    assert result.status == "too_many_files"
    assert list(tmp_base.iterdir()) == []


@pytest.mark.asyncio
async def test_rejects_zip_bomb(sessions_dir: Path, tmp_base: Path) -> None:
    payload = _zip({"big.bin": b"A" * 1024})
    req = TdataConvertRequest(filename="bad.zip", content=payload)
    with patch("core.tdata_import.MAX_UNCOMPRESSED_BYTES", 512):
        result = await convert_tdata_zip(req, sessions_dir, tmp_base=tmp_base)
    assert result.status == "zip_too_large"
    assert list(tmp_base.iterdir()) == []


@pytest.mark.asyncio
async def test_zip_too_large_counts_bytes_written_across_members(
    sessions_dir: Path,
    tmp_base: Path,
) -> None:
    # Two members each under the cap but together over it: the extractor accumulates
    # the bytes it actually writes across members (not the archive's declared sizes)
    # and aborts mid-extraction, leaving nothing behind.
    payload = _zip({"a.bin": b"A" * 400, "b.bin": b"B" * 400})
    req = TdataConvertRequest(filename="bad.zip", content=payload)
    with patch("core.tdata_import.MAX_UNCOMPRESSED_BYTES", 512):
        result = await convert_tdata_zip(req, sessions_dir, tmp_base=tmp_base)
    assert result.status == "zip_too_large"
    assert list(tmp_base.iterdir()) == []


@pytest.mark.asyncio
async def test_tdata_folder_missing(sessions_dir: Path, tmp_base: Path) -> None:
    payload = _zip({"unrelated_dir/something.txt": b"x"})
    req = TdataConvertRequest(filename="bad.zip", content=payload)
    result = await convert_tdata_zip(req, sessions_dir, tmp_base=tmp_base)
    assert result.status == "tdata_not_found"
    assert list(tmp_base.iterdir()) == []


@pytest.mark.asyncio
async def test_no_accounts(sessions_dir: Path, tmp_base: Path) -> None:
    payload = _zip({"tdata/key_data": b"x"})
    req = TdataConvertRequest(filename="empty.zip", content=payload)

    fake_td = MagicMock(accountsCount=0, accounts=[])

    with patch("core.tdata_import.TDesktop", return_value=fake_td):
        result = await convert_tdata_zip(req, sessions_dir, tmp_base=tmp_base)

    assert result.status == "no_accounts"
    assert list(tmp_base.iterdir()) == []


@pytest.mark.asyncio
async def test_happy_path_single_account(sessions_dir: Path, tmp_base: Path) -> None:
    payload = _zip({"tdata/key_data": b"x"})
    req = TdataConvertRequest(filename="good.zip", content=payload)

    fake_client = AsyncMock()
    fake_account = MagicMock()
    fake_account.UserId = 12345
    fake_account.ToTelethon = AsyncMock(return_value=fake_client)
    fake_td = MagicMock(accountsCount=1, accounts=[fake_account])

    with patch("core.tdata_import.TDesktop", return_value=fake_td):
        result = await convert_tdata_zip(req, sessions_dir, tmp_base=tmp_base)

    assert result.status == "ok"
    assert len(result.accounts) == 1
    summary = result.accounts[0]
    assert summary.user_id == 12345
    assert summary.session_path.endswith("12345.session")
    fake_client.disconnect.assert_awaited_once()
    assert list(tmp_base.iterdir()) == []


@pytest.mark.asyncio
async def test_structured_tdata_logs_never_publish_absolute_credential_paths(
    sessions_dir: Path,
    tmp_base: Path,
) -> None:
    payload = _zip({"tdata/key_data": b"x"})
    req = TdataConvertRequest(filename="good.zip", content=payload)
    fake_account = MagicMock(UserId=12345)
    fake_account.ToTelethon = AsyncMock(return_value=AsyncMock())
    fake_td = MagicMock(accountsCount=1, accounts=[fake_account])
    extras: list[dict[str, object]] = []

    async def _capture(
        _level: str,
        _event: str,
        *,
        extra: dict[str, object] | None = None,
        **_kwargs: object,
    ) -> None:
        extras.append(extra or {})

    with (
        patch("core.tdata_import.TDesktop", return_value=fake_td),
        patch("core.tdata_import.log_event", side_effect=_capture),
    ):
        result = await convert_tdata_zip(req, sessions_dir, tmp_base=tmp_base)

    assert result.status == "ok"
    serialized = str(extras)
    assert str(tmp_base) not in serialized
    assert str(sessions_dir) not in serialized
    assert any(extra.get("session_file") == "12345.session" for extra in extras)


@pytest.mark.asyncio
async def test_convert_accepts_content_path_streaming(
    sessions_dir: Path,
    tmp_base: Path,
    tmp_path: Path,
) -> None:
    """convert_tdata_zip must read the archive from disk when content_path is set."""
    payload = _zip({"tdata/key_data": b"x"})
    archive = tmp_path / "tdata.zip"
    archive.write_bytes(payload)
    req = TdataConvertRequest(filename="good.zip", content_path=archive)

    fake_client = AsyncMock()
    fake_account = MagicMock()
    fake_account.UserId = 999
    fake_account.ToTelethon = AsyncMock(return_value=fake_client)
    fake_td = MagicMock(accountsCount=1, accounts=[fake_account])

    with patch("core.tdata_import.TDesktop", return_value=fake_td):
        result = await convert_tdata_zip(req, sessions_dir, tmp_base=tmp_base)

    assert result.status == "ok"
    assert result.accounts[0].user_id == 999


@pytest.mark.asyncio
async def test_happy_path_multiple_accounts(sessions_dir: Path, tmp_base: Path) -> None:
    payload = _zip({"tdata/key_data": b"x"})
    req = TdataConvertRequest(filename="good.zip", content=payload)

    accounts = []
    for uid in (111, 222, 333):
        acc = MagicMock()
        acc.UserId = uid
        acc.ToTelethon = AsyncMock(return_value=AsyncMock())
        accounts.append(acc)

    fake_td = MagicMock(accountsCount=3, accounts=accounts)

    with patch("core.tdata_import.TDesktop", return_value=fake_td):
        result = await convert_tdata_zip(req, sessions_dir, tmp_base=tmp_base)

    assert result.status == "ok"
    assert [a.user_id for a in result.accounts] == [111, 222, 333]
    assert list(tmp_base.iterdir()) == []


@pytest.mark.asyncio
async def test_conversion_error_keeps_partial_summary(
    sessions_dir: Path,
    tmp_base: Path,
) -> None:
    payload = _zip({"tdata/key_data": b"x"})
    req = TdataConvertRequest(filename="good.zip", content=payload)

    good = MagicMock()
    good.UserId = 111
    good.ToTelethon = AsyncMock(return_value=AsyncMock())

    bad = MagicMock()
    bad.UserId = 222
    bad.ToTelethon = AsyncMock(side_effect=RuntimeError("boom"))

    fake_td = MagicMock(accountsCount=2, accounts=[good, bad])

    with patch("core.tdata_import.TDesktop", return_value=fake_td):
        result = await convert_tdata_zip(req, sessions_dir, tmp_base=tmp_base)

    assert result.status == "conversion_error"
    assert result.error == "RuntimeError"
    assert len(result.accounts) == 1
    assert result.accounts[0].user_id == 111
    assert list(tmp_base.iterdir()) == []


@pytest.mark.asyncio
async def test_tdesktop_load_failure_reports_only_the_exception_class(
    sessions_dir: Path,
    tmp_base: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    r"""``error`` must be the class name — this value reaches the HTTP 400 body.

    opentele2's ``OpenTeleException.__str__`` is assembled from the RAISING FRAME'S
    PARAMETER VALUES, and the loader is invoked with ``basePath=str(tdata_dir)``, so
    its text carries the tdata staging path and any proxy the frame held — with
    credentials. ``services.accounts._tdata`` raises a ``ValueError`` from this
    result and ``service_errors_to_http`` renders it verbatim as the 400 ``message``
    (non-negotiable #12).

    The ``log_event`` extra is checked for the same reason and NOT as belt-and-braces:
    ``core.logging.log_event`` persists ``extra`` to the ``logs`` table, ``GET /logs``
    serves it as ``LogEntry.extra`` and ``GET /events`` streams it — so an unbounded
    ``extra`` is an HTTP body by another route. Process logs are also treated as a
    security boundary because operators commonly ship them to external collectors.

    The stand-in below is shaped like the real thing. Pre-fix ``error`` was
    ``f"TDesktop load failed: {exc}"`` and the log extra carried ``str(exc)``, so
    both the path and ``bob:hunter2@…`` were in each and every negative assertion
    below failed. Standard process logs must not retain it either.
    """
    payload = _zip({"tdata/key_data": b"x"})
    req = TdataConvertRequest(filename="good.zip", content=payload)
    leaky = RuntimeError(
        "failed to decrypt C:/Users/op/tdata_staging_x9/tdata/key_datas "
        "via socks5://bob:hunter2@10.20.30.40:1080",
    )
    extras: list[object] = []

    async def fake_log(
        _level: str,
        _event: str,
        account_id: str | None = None,  # noqa: ARG001
        extra: dict[str, object] | None = None,
    ) -> None:
        extras.append(extra or {})

    monkeypatch.setattr("core.tdata_import.log_event", fake_log)

    with patch("core.tdata_import.TDesktop", side_effect=leaky):
        result = await convert_tdata_zip(req, sessions_dir, tmp_base=tmp_base)

    assert result.status == "conversion_error"
    assert result.error == "RuntimeError"
    assert "hunter2" not in (result.error or "")
    assert "hunter2" not in caplog.text
    assert "C:/Users/op" not in caplog.text
    assert "tdata_staging_x9" not in (result.error or "")
    persisted = repr(extras)
    assert "hunter2" not in persisted
    assert "failed to decrypt" not in persisted
    assert list(tmp_base.iterdir()) == []
