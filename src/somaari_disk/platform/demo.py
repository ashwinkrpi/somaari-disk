"""Demo mode: a generated sample tree on fake drives, for screenshots and trying the UI.

Names are invented; nothing here comes from a real disk.
"""

from __future__ import annotations

import random
from pathlib import Path

from .base import Volume
from .fake import FakeDir, FakeFile, FakePlatform

DEMO_ROOT = Path("demo")
GB = 10**9
MB = 10**6
_DATE = 1_780_000_000  # fixed epoch so screenshots are stable

_FOLDERS = {
    "Videos": ("mp4", 400 * MB, 4 * GB),
    "Photos": ("jpg", 2 * MB, 12 * MB),
    "Music": ("flac", 20 * MB, 60 * MB),
    "Projects": ("zip", 1 * MB, 900 * MB),
    "Documents": ("pdf", 50_000, 8 * MB),
    "Downloads": ("iso", 5 * MB, 3 * GB),
    "Games": ("pak", 100 * MB, 6 * GB),
    "Backups": ("tar", 200 * MB, 9 * GB),
}


def _files(rng: random.Random, prefix: str, ext: str, lo: int, hi: int, n: int) -> dict:
    day = 86_400
    return {
        f"{prefix} {i + 1:03d}.{ext}": FakeFile(
            rng.randint(lo, hi), mtime=_DATE - i * day, created=_DATE - (i + 30) * day
        )
        for i in range(n)
    }


def demo_tree(seed: int = 7) -> FakeDir:
    rng = random.Random(seed)
    top: dict = {}
    for name, (ext, lo, hi) in _FOLDERS.items():
        sub = {
            f"{name} {year}": FakeDir(_files(rng, "Item", ext, lo, hi, rng.randint(3, 25)))
            for year in range(2019, 2019 + rng.randint(2, 6))
        }
        sub.update(_files(rng, name.rstrip("s"), ext, lo, hi, rng.randint(2, 8)))
        top[name] = FakeDir(sub, mtime=_DATE)
    top["notes.txt"] = FakeFile(4_200, mtime=_DATE)
    top[".cache"] = FakeDir(_files(rng, "blob", "bin", 1 * MB, 40 * MB, 30))
    return FakeDir(top, mtime=_DATE)


def demo_platform() -> FakePlatform:
    internal = DEMO_ROOT / "Internal"
    usb = DEMO_ROOT / "USB stick"
    tree = FakeDir(
        {
            "Internal": demo_tree(7),
            "USB stick": FakeDir({"Photos": demo_tree(11).children["Photos"]}, dev=2),
        },
        dev=1,
    )
    volumes = [
        Volume("demo-internal", "Internal disk", internal, "ext4", 256 * GB, 141 * GB, 115 * GB,
               device_name="disk0", is_system=True),
        Volume("demo-usb", "USB stick", usb, "exfat", 64 * GB, 52 * GB, 12 * GB,
               is_removable=True, device_name="usb0"),
    ]  # fmt: skip
    return FakePlatform(tree, root=DEMO_ROOT, volumes=volumes)
