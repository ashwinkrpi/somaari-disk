"""Command line: `scan <path>` prints the largest items, `volumes` lists drives.

A frontend, so it formats sizes.
"""

from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path

from .core.models import ScanProgress, ScanResult
from .core.scanner import Scanner
from .core.tree import largest_files, path_segments, top_children
from .platform import Platform, get_platform


def fmt_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1000 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1000
    raise AssertionError("unreachable")


def _progress(p: ScanProgress) -> None:
    sys.stderr.write(f"\r{p.files} files, {p.dirs} folders, {fmt_size(p.size)}   ")
    sys.stderr.flush()


def _run(scanner: Scanner, root: Path) -> ScanResult:
    """Scan off the main thread so Ctrl+C can cancel cleanly."""
    box: list[ScanResult] = []
    t = threading.Thread(target=lambda: box.append(scanner.scan(root)), daemon=True)
    t.start()
    while t.is_alive():
        try:
            t.join(0.2)
        except KeyboardInterrupt:
            scanner.cancel()
    return box[0]


def print_report(result: ScanResult, top: int, files: int, by: str, out=None) -> None:
    out = out or sys.stdout  # resolved per call so redirected stdout is honoured
    root = result.root
    total = getattr(root, by)
    head = f"{result.root_path}  {fmt_size(total)}  ({result.files} files, {result.dirs} folders)"
    print(head, file=out)
    if result.cancelled:
        print("Scan cancelled: results are partial.", file=out)
    if result.volume_lost:
        print("Drive disconnected during the scan: results are partial.", file=out)
    items, rest = top_children(root, top, by)
    for c in items:
        print(f"  {fmt_size(getattr(c, by)):>10}  {c.name}{'/' if c.is_dir else ''}", file=out)
    if rest:
        rest_size = getattr(rest, by)
        print(f"  {fmt_size(rest_size):>10}  Other ({rest.count} items)", file=out)
    if files:
        print("Largest files:", file=out)
        for f in largest_files(root, files, by):
            print(f"  {fmt_size(getattr(f, by)):>10}  {Path(*path_segments(f))}", file=out)
    if result.warnings:
        kinds: dict[str, int] = {}
        for w in result.warnings:
            kinds[w.kind] = kinds.get(w.kind, 0) + 1
        summary = ", ".join(f"{k}: {v}" for k, v in sorted(kinds.items()))
        print(f"Warnings: {summary}", file=out)


def main(argv: list[str] | None = None, platform: Platform | None = None) -> int:
    parser = argparse.ArgumentParser(prog="disk-usage-analyzer scan")
    parser.add_argument("path", type=Path)
    parser.add_argument("--top", type=int, default=15, help="children of the root to list")
    parser.add_argument("--files", type=int, default=10, help="largest files to list")
    parser.add_argument("--on-disk", action="store_true", help="rank by size on disk")
    parser.add_argument("--cross-mounts", action="store_true", help="enter other filesystems")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--quiet", action="store_true", help="no progress on stderr")
    args = parser.parse_args(argv)

    scanner = Scanner(
        platform or get_platform(),
        workers=args.workers,
        cross_mounts=args.cross_mounts,
        progress=None if args.quiet else _progress,
        progress_interval=0.2,
    )
    result = _run(scanner, args.path)
    if not args.quiet:
        sys.stderr.write("\n")
    if not result.root.is_dir and result.files == 0:
        msg = result.warnings[0].message if result.warnings else "not a folder"
        print(f"Cannot scan {args.path}: {msg}", file=sys.stderr)
        return 1
    print_report(result, args.top, args.files, "size_on_disk" if args.on_disk else "size")
    if result.volume_lost:
        return 2
    return 130 if result.cancelled else 0


def volumes_main(argv: list[str] | None = None, platform: Platform | None = None, out=None) -> int:
    out = out or sys.stdout
    argparse.ArgumentParser(prog="disk-usage-analyzer volumes").parse_args(argv)
    for v in (platform or get_platform()).list_volumes():
        flags = [
            name
            for name, on in (
                ("system", v.is_system),
                ("removable", v.is_removable),
                ("network", v.is_network),
                ("read-only", v.is_read_only),
            )
            if on
        ]
        print(
            f"{fmt_size(v.used):>10} / {fmt_size(v.total):<10} {v.fs_type:<8} "
            f"{v.mount_point}  [{v.label}] {v.device_name} {' '.join(flags)}".rstrip(),
            file=out,
        )
    return 0
