"""Files for ``docker cp -``: packed with Android's owners and modes, not the host's.

``docker cp`` of a host path packs it with the host's modes, and from Windows every
file comes out ``0755``: an executable ``build.prop`` or init script, which no phone
has. Packed here, each entry is root's with the mode AOSP's ``fs_config`` gives it: a
file ``0644``, a binary ``0755``, a directory ``0755`` unless told otherwise. Under
/vendor directories and binaries are group ``shell``, as the image's own are.

Unpacking sets the owner and mode of every directory it names, existing ones too, so
a tree names only directories whose owner and mode it gives as the image has them.
``docker cp -a`` keeps these owners (``copy_in``).
"""

from __future__ import annotations

import io
import tarfile
import time
from typing import TYPE_CHECKING

from core.emulator._process import docker

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping
    from pathlib import Path

ROOT = 0
# AID_SHELL: the group of /vendor's directories and binaries.
SHELL = 2000
_GROUP_NAMES = {ROOT: "root", SHELL: "shell"}


def read_tree(directory: Path) -> dict[str, bytes]:
    """Every file under ``directory``: its POSIX path relative to it -> its content."""
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def _entry(name: str, mode: int, gid: int, mtime: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.mode, info.uid, info.gid, info.mtime = mode, ROOT, gid, mtime
    info.uname, info.gname = "root", _GROUP_NAMES.get(gid, "root")
    return info


def pack(
    files: Mapping[str, bytes],
    *,
    group: int = ROOT,
    executables: Collection[str] = (),
    directories: Mapping[str, int] | None = None,
) -> bytes:
    """A tar of ``files`` (POSIX path -> content) and every directory above them.

    Files are ``0644`` root:root; ``executables`` ``0755`` root:``group``; directories
    root:``group``, ``0755`` or their mode in ``directories``.
    """
    modes = directories or {}
    mtime = int(time.time())
    parents = sorted(
        {
            "/".join(parts[:end])
            for parts in (p.split("/") for p in files)
            for end in range(1, len(parts))
        }
    )
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for name in parents:
            info = _entry(name, modes.get(name, 0o755), group, mtime)
            info.type = tarfile.DIRTYPE
            tar.addfile(info)
        for name in sorted(files):
            binary = name in executables
            info = _entry(name, 0o755 if binary else 0o644, group if binary else ROOT, mtime)
            info.size = len(files[name])
            tar.addfile(info, io.BytesIO(files[name]))
    return buffer.getvalue()


async def copy_in(container: str, directory: str, archive: bytes) -> None:
    """Unpack ``archive`` (a ``pack``) into ``directory`` of the stopped ``container``."""
    await docker("cp", "-a", "-", f"{container}:{directory}", stdin=archive)
