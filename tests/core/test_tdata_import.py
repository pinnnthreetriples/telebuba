"""Tests for ``core.tdata_import`` — safe zip extraction + opentele2 wrapper.

The real opentele2 conversion can't run inside the test harness (it needs a real
tdata payload, a real Telegram authorisation, and network). What we DO test for
real:

- zip security validators reject path traversal, absolute paths, too many files,
  zip bombs, POSIX symlinks, and invalid zips with the right status.
- happy path returns ``ok`` and writes a session file (opentele2 mocked).
- failures from opentele2 surface as ``conversion_error`` and include the partial
  summary built so far.
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
    assert result.error is not None
    assert "222" in result.error
    assert len(result.accounts) == 1
    assert result.accounts[0].user_id == 111
    assert list(tmp_base.iterdir()) == []


@pytest.mark.asyncio
async def test_tdesktop_load_failure(sessions_dir: Path, tmp_base: Path) -> None:
    payload = _zip({"tdata/key_data": b"x"})
    req = TdataConvertRequest(filename="good.zip", content=payload)

    with patch("core.tdata_import.TDesktop", side_effect=RuntimeError("bad key")):
        result = await convert_tdata_zip(req, sessions_dir, tmp_base=tmp_base)

    assert result.status == "conversion_error"
    assert result.error is not None
    assert "bad key" in result.error
    assert list(tmp_base.iterdir()) == []
