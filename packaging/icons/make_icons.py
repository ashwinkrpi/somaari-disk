"""Regenerate the PNG and ICO app icons from somaari-disk.svg (needs rsvg-convert).

Outputs are committed so release builds need no image tools:
  src/somaari_disk/ui/icon.png   window icon (Linux)
  packaging/icons/somaari-disk-256.png   .deb hicolor icon
  packaging/icons/somaari-disk.ico   Windows exe + installer icon (PNG-in-ICO, Vista+)
"""

import struct
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
SVG = HERE / "somaari-disk.svg"
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def render(size: int) -> bytes:
    cmd = ["rsvg-convert", "-w", str(size), "-h", str(size), str(SVG)]
    return subprocess.run(cmd, check=True, capture_output=True).stdout


def pack_ico(pngs: dict[int, bytes]) -> bytes:
    """ICONDIR + one ICONDIRENTRY per size, each image stored as PNG."""
    out = struct.pack("<HHH", 0, 1, len(pngs))
    offset = 6 + 16 * len(pngs)
    body = b""
    for size, data in sorted(pngs.items()):
        dim = 0 if size >= 256 else size  # 0 means 256
        out += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset + len(body))
        body += data
    return out + body


def main() -> int:
    pngs = {size: render(size) for size in ICO_SIZES}
    (ROOT / "src" / "somaari_disk" / "ui" / "icon.png").write_bytes(pngs[256])
    (HERE / "somaari-disk-256.png").write_bytes(pngs[256])
    (HERE / "somaari-disk.ico").write_bytes(pack_ico(pngs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
