"""Build dist/somaari-disk_<version>_all.deb (Debian, Ubuntu, Raspberry Pi OS; arm64 + x86_64).

One architecture-independent package: GTK, WebKitGTK, PyGObject and psutil (compiled) come from
apt via Depends; the pure-Python dependencies are vendored into /usr/lib/somaari-disk.
Needs pip and dpkg-deb. Run from the repo root: python3 packaging/linux/build_deb.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NAME = "somaari-disk"
LIB = Path("usr", "lib", NAME)
# pure-Python runtime deps vendored into LIB (psutil comes from apt: it has C extensions)
VENDORED = ["pywebview", "bottle", "proxy_tools", "typing_extensions", "send2trash", "platformdirs"]
DEPENDS = [
    "python3 (>= 3.11)",
    "python3-gi",
    "gir1.2-gtk-3.0",
    "gir1.2-webkit2-4.1 | gir1.2-webkit2-4.0",
    "python3-psutil (>= 5.9)",
]
# pywebview ships Windows/Android binaries under webview/lib; Linux never loads them
PRUNE = [Path("webview", "lib"), Path("bin")]
COMPILED_SUFFIXES = {".so", ".pyd", ".dll", ".dylib", ".exe", ".jar"}

LAUNCHER = f"""#!/usr/bin/python3
import sys

sys.path.insert(0, "/{LIB.as_posix()}")
from somaari_disk.__main__ import main

sys.exit(main())
"""

DESKTOP = f"""[Desktop Entry]
Type=Application
Name=Somaari Disk
GenericName=Disk Usage
Comment=See what is using space on your drives
Exec={NAME}
Icon={NAME}
Terminal=false
Categories=System;Filesystem;
Keywords=disk;space;storage;usage;folder;
StartupWMClass={NAME}
"""

POSTINST = f"""#!/bin/sh
set -e
if [ "$1" = configure ]; then
    python3 -m compileall -q /{LIB.as_posix()} >/dev/null 2>&1 || true
fi
"""

PRERM = f"""#!/bin/sh
set -e
find /{LIB.as_posix()} -name __pycache__ -type d -prune -exec rm -r {{}} + 2>/dev/null || true
"""


def project() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def maintainer(meta: dict) -> str:
    author = meta["authors"][0]
    return f"{author['name']} <{author['email']}>"


def control(meta: dict, installed_kib: int) -> str:
    return (
        f"Package: {NAME}\n"
        f"Version: {meta['version']}\n"
        "Architecture: all\n"
        f"Maintainer: {maintainer(meta)}\n"
        f"Installed-Size: {installed_kib}\n"
        f"Depends: {', '.join(DEPENDS)}\n"
        "Section: utils\n"
        "Priority: optional\n"
        f"Homepage: {meta['urls']['Homepage']}\n"
        f"Description: {meta['description']}\n"
        " Shows what is using space on internal and external drives, largest items first.\n"
        " Read-only except Move to Trash. No network access.\n"
    )


def pip_install_vendored(target: Path) -> None:
    """Install this project + pure-Python deps into target (network: PyPI, build time only)."""
    cmd = [sys.executable, "-m", "pip", "install", "--quiet", "--no-compile", "--no-deps"]
    cmd += ["--target", str(target), str(ROOT), *VENDORED]
    subprocess.run(cmd, check=True)


def write(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    path.chmod(mode)


def stage(staging: Path, meta: dict, install: Callable[[Path], None]) -> None:
    """Lay out the package tree under staging (DEBIAN/ + usr/)."""
    lib = staging / LIB
    lib.mkdir(parents=True)
    install(lib)
    for rel in PRUNE:
        shutil.rmtree(lib / rel, ignore_errors=True)
    for cache in list(lib.rglob("__pycache__")):
        shutil.rmtree(cache)
    compiled = [p for p in lib.rglob("*") if p.suffix.lower() in COMPILED_SUFFIXES]
    if compiled:
        names = ", ".join(str(p.relative_to(lib)) for p in compiled[:5])
        raise SystemExit(f"compiled files in vendored deps (must come from apt): {names}")

    usr = staging / "usr"
    write(usr / "bin" / NAME, LAUNCHER, 0o755)
    write(usr / "share" / "applications" / f"{NAME}.desktop", DESKTOP)
    icons = usr / "share" / "icons" / "hicolor"
    (icons / "scalable" / "apps").mkdir(parents=True)
    (icons / "256x256" / "apps").mkdir(parents=True)
    shutil.copyfile(ROOT / "packaging/icons/somaari-disk.svg", icons / f"scalable/apps/{NAME}.svg")
    shutil.copyfile(
        ROOT / "packaging/icons/somaari-disk-256.png", icons / f"256x256/apps/{NAME}.png"
    )
    copyright_text = "\n\n".join(
        (ROOT / name).read_text(encoding="utf-8") for name in ("LICENSE", "THIRD-PARTY-NOTICES.md")
    )
    write(usr / "share" / "doc" / NAME / "copyright", copyright_text)

    for path in staging.rglob("*"):
        if path.is_dir():
            path.chmod(0o755)
        elif not os.access(path, os.X_OK):
            path.chmod(0o644)

    size = sum(p.stat().st_size for p in usr.rglob("*") if p.is_file())
    write(staging / "DEBIAN" / "control", control(meta, (size + 1023) // 1024))
    write(staging / "DEBIAN" / "postinst", POSTINST, 0o755)
    write(staging / "DEBIAN" / "prerm", PRERM, 0o755)


def main() -> int:
    meta = project()
    build = ROOT / "build" / "deb"
    staging = build / f"{NAME}_{meta['version']}_all"
    shutil.rmtree(build, ignore_errors=True)
    stage(staging, meta, pip_install_vendored)
    out = ROOT / "dist" / f"{NAME}_{meta['version']}_all.deb"
    out.parent.mkdir(exist_ok=True)
    cmd = ["dpkg-deb", "--root-owner-group", "-Zxz", "--build", str(staging), str(out)]
    subprocess.run(cmd, check=True)
    print(out.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
