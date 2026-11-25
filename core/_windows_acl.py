"""NTFS access lists of the private paths: read them, and (only on command) narrow them.

The live install runs on Windows, where the POSIX modes of ``core.secure_paths`` mean
nothing. A folder created next to the app inherits whatever its parent grants: on a second
disk (the usual home of the session-vault mirror) the root grants "Authenticated Users"
access, so every local account could read the fleet's logins.

The list is read as an SDDL string (``GetNamedSecurityInfoW`` and
``ConvertSecurityDescriptorToStringSecurityDescriptorW``), which does not depend on the
language of the system: ``icacls`` output is localised and is never parsed here.
:func:`broad_grants_in_sddl` is the pure half and works on any OS.

Narrowing runs ``icacls`` and is called only by ``python -m tools.secure_paths fix``: nothing
rewrites the access list of a live credential folder by itself.
"""

from __future__ import annotations

import contextlib
import ctypes
import re
import subprocess  # nosec B404 - the one fixed Windows tool, argv list, no shell
import sys
from ctypes import wintypes
from pathlib import Path

_SE_FILE_OBJECT = 1
_DACL_SECURITY_INFORMATION = 4
_SDDL_REVISION_1 = 1
_TOKEN_QUERY = 8
_TOKEN_USER = 1
_ICACLS_TIMEOUT_SECONDS = 120

# SYSTEM and the Administrators group: they keep full access when the list is narrowed.
_SYSTEM_SID = "S-1-5-18"
_ADMINISTRATORS_SID = "S-1-5-32-544"

# Principals that stand for "everyone on this computer": SDDL alias or SID -> stable code.
_BROAD_PRINCIPALS = {
    "WD": "everyone",
    "S-1-1-0": "everyone",
    "AU": "authenticated_users",
    "S-1-5-11": "authenticated_users",
    "BU": "users",
    "S-1-5-32-545": "users",
    "IU": "interactive",
    "S-1-5-4": "interactive",
    "AN": "anonymous",
    "S-1-5-7": "anonymous",
    "NU": "network",
    "S-1-5-2": "network",
    "BG": "guests",
    "S-1-5-32-546": "guests",
}

_ACE_FIELDS = 6
_INHERIT_ONLY = "IO"
# Allow entries: plain, and conditional (``XA``, whose last field is a parenthesised condition).
_ALLOW_TYPES = frozenset({"A", "XA"})
_NULL_DACL = "NO_ACCESS_CONTROL"
# "Domain Users" has a per-domain SID that ends in the well-known RID 513.
_DOMAIN_USERS = re.compile(r"S-1-5-21(?:-\d+){3}-513", re.IGNORECASE)


class AclError(RuntimeError):
    """Reading or narrowing an access list failed; carries a code, never a path."""

    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


def _split_dacl(sddl: str) -> tuple[str, list[str]] | None:
    """The DACL's flags text and its ACE bodies, or ``None`` when ``sddl`` has no ``D:`` part.

    Scans parentheses by depth (and skips quoted text), so the nested parentheses of a
    conditional ACE's condition stay inside their entry. The SACL (``S:``) is not read.
    """
    start = sddl.find("D:")
    if start < 0:
        return None
    flags: list[str] = []
    aces: list[str] = []
    depth = 0
    quoted = False
    begin = 0
    index = start + 2
    while index < len(sddl):
        char = sddl[index]
        if char == '"' and depth:
            quoted = not quoted
        elif not quoted and char == "(":
            if depth == 0:
                begin = index + 1
            depth += 1
        elif not quoted and char == ")" and depth:
            depth -= 1
            if depth == 0:
                aces.append(sddl[begin:index])
        elif depth == 0 and not quoted:
            if sddl.startswith("S:", index):
                break
            flags.append(char)
        index += 1
    return "".join(flags), aces


def _grants_something(rights: str) -> bool:
    if rights.lower().startswith("0x"):
        try:
            return int(rights, 16) != 0
        except ValueError:
            return False
    return bool(rights)


def _is_inherit_only(flags: str) -> bool:
    return _INHERIT_ONLY in (flags[i : i + 2] for i in range(0, len(flags), 2))


def _broad_code(principal: str) -> str | None:
    principal = principal.upper()
    if _DOMAIN_USERS.fullmatch(principal):
        return "domain_users"
    return _BROAD_PRINCIPALS.get(principal)


def broad_grants_in_sddl(sddl: str) -> list[str]:
    """The broad principals an SDDL string ALLOWS some access to, as stable codes.

    Deny entries, entries only meant for children (inherit-only) and entries without
    rights are not grants. A NULL DACL (``D:NO_ACCESS_CONTROL``, or a non-empty string
    without a ``D:`` part) grants everyone everything. Order of first appearance, no
    repeats.
    """
    if not sddl:
        return []
    dacl = _split_dacl(sddl)
    if dacl is None or _NULL_DACL in dacl[0]:
        return ["everyone"]
    found: list[str] = []
    for ace in dacl[1]:
        fields = ace.split(";", _ACE_FIELDS)
        if len(fields) < _ACE_FIELDS or fields[0] not in _ALLOW_TYPES:
            continue
        if _is_inherit_only(fields[1]) or not _grants_something(fields[2]):
            continue
        code = _broad_code(fields[5])
        if code is not None and code not in found:
            found.append(code)
    return found


def _advapi32() -> ctypes.WinDLL:
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
    ]
    advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = wintypes.BOOL
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    return advapi32


def _kernel32() -> ctypes.WinDLL:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.GetSystemDirectoryW.argtypes = [wintypes.LPWSTR, wintypes.UINT]
    kernel32.GetSystemDirectoryW.restype = wintypes.UINT
    return kernel32


def _take_string(pointer: ctypes.c_void_p, kernel32: ctypes.WinDLL) -> str:
    """The text a Windows call allocated at ``pointer``; the allocation is freed."""
    try:
        return ctypes.wstring_at(pointer.value or 0)
    finally:
        kernel32.LocalFree(pointer)


def read_sddl(path: Path) -> str:
    """The DACL of ``path`` as an SDDL string. Windows only."""
    if sys.platform != "win32":
        msg = "NTFS access lists exist on Windows only"
        raise AclError(msg)
    advapi32, kernel32 = _advapi32(), _kernel32()
    descriptor = ctypes.c_void_p()
    # A junction or symlink has a list of its own; the data lives behind it.
    with contextlib.suppress(OSError):
        path = path.resolve()
    status = advapi32.GetNamedSecurityInfoW(
        str(path),
        _SE_FILE_OBJECT,
        _DACL_SECURITY_INFORMATION,
        None,
        None,
        None,
        None,
        ctypes.byref(descriptor),
    )
    if status != 0:
        msg = f"GetNamedSecurityInfoW failed with code {status}"
        raise AclError(msg, code=int(status))
    try:
        text = ctypes.c_void_p()
        if not advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW(
            descriptor, _SDDL_REVISION_1, _DACL_SECURITY_INFORMATION, ctypes.byref(text), None
        ):
            code = ctypes.get_last_error()
            msg = f"SDDL conversion failed with code {code}"
            raise AclError(msg, code=code)
        return _take_string(text, kernel32)
    finally:
        kernel32.LocalFree(descriptor)


def broad_grants(path: Path) -> list[str]:
    """The broad principals (everyone, all signed-in users, ...) that ``path`` grants access to."""
    return broad_grants_in_sddl(read_sddl(path))


def current_user_sid() -> str:
    """The SID of the user this process runs as (the token's owner, whatever the language)."""
    advapi32, kernel32 = _advapi32(), _kernel32()
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)
    ):
        code = ctypes.get_last_error()
        msg = f"OpenProcessToken failed with code {code}"
        raise AclError(msg, code=code)
    try:
        size = wintypes.DWORD()
        advapi32.GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(token, _TOKEN_USER, buffer, size, ctypes.byref(size)):
            code = ctypes.get_last_error()
            msg = f"GetTokenInformation failed with code {code}"
            raise AclError(msg, code=code)
        # TOKEN_USER starts with a SID_AND_ATTRIBUTES, whose first field is the SID pointer.
        sid = ctypes.c_void_p.from_buffer(buffer)
        text = ctypes.c_void_p()
        if not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            code = ctypes.get_last_error()
            msg = f"ConvertSidToStringSidW failed with code {code}"
            raise AclError(msg, code=code)
        return _take_string(text, kernel32)
    finally:
        kernel32.CloseHandle(token)


def _icacls() -> str:
    kernel32 = _kernel32()
    buffer = ctypes.create_unicode_buffer(260)
    if not kernel32.GetSystemDirectoryW(buffer, len(buffer)):
        msg = "system directory unknown"
        raise AclError(msg, code=ctypes.get_last_error())
    return str(Path(buffer.value) / "icacls.exe")


def restrict_to_owner(path: Path) -> None:
    """Leave only the current user, SYSTEM and Administrators with access to ``path``.

    Inheritance from the parent is cut, the broad principals are removed and the three get
    full control (for a folder: inherited by everything inside it). SIDs, never account
    names, so any system language works.
    Raises :class:`AclError` with ``icacls``'s exit code on failure.
    """
    if sys.platform != "win32":
        msg = "NTFS access lists exist on Windows only"
        raise AclError(msg)
    right = "(OI)(CI)F" if path.is_dir() else "F"
    # ``/inheritance:r`` drops what the parent hands down; an entry written on the object
    # itself for a broad principal has to be removed by name.
    broad_sids = [sid for sid in _BROAD_PRINCIPALS if sid.startswith("S-")]
    broad_sids += sorted(set(_DOMAIN_USERS.findall(read_sddl(path))))
    command = [
        _icacls(),
        str(path),
        "/inheritance:r",
        "/remove:g",
        *(f"*{sid}" for sid in broad_sids),
    ]
    for sid in (current_user_sid(), _SYSTEM_SID, _ADMINISTRATORS_SID):
        command += ["/grant:r", f"*{sid}:{right}"]
    done = subprocess.run(  # noqa: S603  # nosec B603 - absolute tool, argv list, no shell
        command,
        check=False,
        capture_output=True,
        timeout=_ICACLS_TIMEOUT_SECONDS,
    )
    if done.returncode != 0:
        msg = f"icacls failed with exit code {done.returncode}"
        raise AclError(msg, code=done.returncode)


__all__ = [
    "AclError",
    "broad_grants",
    "broad_grants_in_sddl",
    "current_user_sid",
    "read_sddl",
    "restrict_to_owner",
]
