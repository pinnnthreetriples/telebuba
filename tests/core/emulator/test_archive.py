"""What ``docker cp -`` unpacks into a container: root's files with AOSP's modes."""

from __future__ import annotations

import io
import tarfile
from typing import TYPE_CHECKING

from core.emulator import _archive

if TYPE_CHECKING:
    from pathlib import Path


def _entries(data: bytes) -> dict[str, tarfile.TarInfo]:
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        return {info.name: info for info in tar.getmembers()}


def test_a_file_is_roots_and_not_executable() -> None:
    (prop,) = _entries(_archive.pack({"build.prop": b"a=b\n"})).values()

    assert prop.name == "build.prop"
    assert prop.isfile()
    assert (prop.mode, prop.uid, prop.gid) == (0o644, 0, 0)
    assert prop.size == len(b"a=b\n")


def test_a_binary_and_the_directories_take_the_group_given() -> None:
    entries = _entries(
        _archive.pack(
            {"bin/hw/service": b"\x7fELF", "etc/init/service.rc": b"service x\n"},
            group=_archive.SHELL,
            executables={"bin/hw/service"},
            directories={"bin": 0o751, "bin/hw": 0o751},
        )
    )

    modes = {name: (info.mode, info.uid, info.gid) for name, info in entries.items()}
    assert modes == {
        "bin": (0o751, 0, 2000),
        "bin/hw": (0o751, 0, 2000),
        "etc": (0o755, 0, 2000),
        "etc/init": (0o755, 0, 2000),
        "bin/hw/service": (0o755, 0, 2000),
        "etc/init/service.rc": (0o644, 0, 0),
    }
    assert list(entries).index("bin/hw") < list(entries).index("bin/hw/service"), (
        "a directory comes before what it holds"
    )


def test_a_tree_is_read_as_posix_paths(tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    (tree / "overlay" / "x").mkdir(parents=True)
    (tree / "overlay" / "x" / "x.apk").write_bytes(b"PK")
    (tree / "top.xml").write_text("<a/>", encoding="utf-8")

    assert _archive.read_tree(tree) == {"overlay/x/x.apk": b"PK", "top.xml": b"<a/>"}
