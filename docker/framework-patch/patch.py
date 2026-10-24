"""Inject signature spoofing into a redroid image's ``services.jar`` (docs/redroid.md).

Usage: ``patch.py <services.jar in> <services.jar out>``. Disassembles the jar's dex
files with baksmali, drops in the ``FakeSig`` helper beside ``ComputerEngine``, injects
one call to it right after ``ComputerEngine.generatePackageInfo`` has built its
``PackageInfo``, reassembles with smali and repacks the jar. Standard library only; the
JDK, baksmali and smali come from the image this runs in.
"""

from __future__ import annotations

import re
import subprocess
import sys
import zipfile
from pathlib import Path

_WORK = Path("/w")
_DEX = re.compile(r"classes\d*\.dex")
# The call PackageInfoUtils makes that yields the PackageInfo, and the fields that follow.
_GENERATE = "PackageInfoUtils;->generate("
_MOVE = "move-result-object "
_AFTER = "Landroid/content/pm/PackageInfo;->applicationInfo:"
_HELPER = "Lcom/android/server/pm/FakeSig;"


def _disassemble(jar: Path, dex_dir: Path, smali_dir: Path) -> list[str]:
    with zipfile.ZipFile(jar) as archive:
        dexes = [name for name in archive.namelist() if _DEX.fullmatch(name)]
        dex_dir.mkdir(parents=True, exist_ok=True)
        for name in dexes:
            (dex_dir / name).write_bytes(archive.read(name))
    for name in dexes:
        out = smali_dir / name.removesuffix(".dex")
        subprocess.run(
            ["java", "-jar", str(_WORK / "baksmali.jar"), "d", str(dex_dir / name), "-o", str(out)],
            check=True,
        )
    return dexes


def _inject(computer_engine: Path) -> None:
    """Add the ``FakeSig.maybe`` call after the generated PackageInfo, idempotently."""
    lines = computer_engine.read_text(encoding="utf-8").splitlines()
    if any(_HELPER in line for line in lines):
        return
    generate = next(i for i, line in enumerate(lines) if _GENERATE in line)
    move = next(i for i in range(generate + 1, len(lines)) if lines[i].strip().startswith(_MOVE))
    register = lines[move].split()[-1]
    target = next(i for i in range(move + 1, len(lines)) if _AFTER in lines[i])
    call = [
        f"    invoke-static {{{register}}}, {_HELPER}->maybe"
        "(Landroid/content/pm/PackageInfo;)Landroid/content/pm/PackageInfo;",
        "",
        f"    move-result-object {register}",
        "",
    ]
    lines[target:target] = call
    computer_engine.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _assemble(smali_dir: Path, dexes: list[str], new_dir: Path) -> None:
    new_dir.mkdir(parents=True, exist_ok=True)
    for name in dexes:
        source = smali_dir / name.removesuffix(".dex")
        subprocess.run(
            ["java", "-jar", str(_WORK / "smali.jar"), "a", str(source), "-o", str(new_dir / name)],
            check=True,
        )


def _repack(jar_in: Path, new_dir: Path, jar_out: Path) -> None:
    """Copy ``jar_in`` to ``jar_out`` with its dex files replaced by the reassembled ones."""
    jar_out.parent.mkdir(parents=True, exist_ok=True)
    with (
        zipfile.ZipFile(jar_in) as source,
        zipfile.ZipFile(jar_out, "w", zipfile.ZIP_DEFLATED) as out,
    ):
        for item in source.infolist():
            if not _DEX.fullmatch(item.filename):
                out.writestr(item, source.read(item.filename))
        for dex in sorted(new_dir.glob("*.dex")):
            out.write(dex, dex.name)


def main() -> None:
    jar_in, jar_out = Path(sys.argv[1]), Path(sys.argv[2])
    smali_dir = _WORK / "smali"
    dexes = _disassemble(jar_in, _WORK / "dex", smali_dir)
    computer_engine = next(smali_dir.rglob("ComputerEngine.smali"))
    _inject(computer_engine)
    cert = (_WORK / "google_cert.hex").read_text(encoding="ascii").strip()
    template = (_WORK / "fakesig.smali.tmpl").read_text(encoding="utf-8")
    (computer_engine.parent / "FakeSig.smali").write_text(
        template.replace("@CERT@", cert), encoding="utf-8"
    )
    _assemble(smali_dir, dexes, _WORK / "new")
    _repack(jar_in, _WORK / "new", jar_out)


if __name__ == "__main__":
    main()
