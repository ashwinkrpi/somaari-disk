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
from .core.tree import display_name, largest_files, path_segments, top_children
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
    counts = f"({result.files} files, {result.dirs} folders)"
    head = f"{display_name(str(result.root_path))}  {fmt_size(total)}  {counts}"
    print(head, file=out)
    if result.cancelled:
        print("Scan cancelled: results are partial.", file=out)
    if result.volume_lost:
        print("Drive disconnected during the scan: results are partial.", file=out)
    items, rest = top_children(root, top, by)
    for c in items:
        print(
            f"  {fmt_size(getattr(c, by)):>10}  {display_name(c.name)}{'/' if c.is_dir else ''}",
            file=out,
        )
    if rest:
        rest_size = getattr(rest, by)
        print(f"  {fmt_size(rest_size):>10}  Other ({rest.count} items)", file=out)
    if files:
        print("Largest files:", file=out)
        for f in largest_files(root, files, by):
            print(
                f"  {fmt_size(getattr(f, by)):>10}  {display_name(str(Path(*path_segments(f))))}",
                file=out,
            )
    if result.warnings:
        kinds: dict[str, int] = {}
        for w in result.warnings:
            kinds[w.kind] = kinds.get(w.kind, 0) + 1
        summary = ", ".join(f"{k}: {v}" for k, v in sorted(kinds.items()))
        print(f"Warnings: {summary}", file=out)


def main(argv: list[str] | None = None, platform: Platform | None = None) -> int:
    parser = argparse.ArgumentParser(prog="somaari-disk scan")
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
        print(f"Cannot scan {display_name(str(args.path))}: {msg}", file=sys.stderr)
        return 1
    print_report(result, args.top, args.files, "size_on_disk" if args.on_disk else "size")
    if result.volume_lost:
        return 2
    return 130 if result.cancelled else 0


def volumes_main(argv: list[str] | None = None, platform: Platform | None = None, out=None) -> int:
    out = out or sys.stdout
    argparse.ArgumentParser(prog="somaari-disk volumes").parse_args(argv)
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


def _rss() -> int:
    import psutil  # lazy: only the bench needs it here

    return psutil.Process().memory_info().rss


def bench_tree(files: int, per_dir: int = 50, fan_out: int = 5):
    """Generated tree with `files` files (odd names included), as a FakeDir."""
    from .platform.fake import FakeDir, FakeFile

    odd = ("with space", "emoji \U0001f4e6", "bad-\udcff-utf8", "x" * 200)
    root = FakeDir()
    level = [root]
    made = 0
    while made < files:
        nxt = []
        for d in level:
            for _ in range(per_dir):
                if made >= files:
                    break
                name = f"f{made:07d}.bin" if made % 997 else f"{odd[made % 4]} {made}"
                d.children[name] = FakeFile(size=(made * 7919) % 5_000_000, mtime=made)
                made += 1
            for j in range(fan_out if made < files else 0):
                sub = FakeDir()
                d.children[f"d{j:02d}"] = sub
                nxt.append(sub)
        level = nxt
    return root


def bench_main(argv: list[str] | None = None, out=None) -> int:
    """Scan a generated tree in memory: time, tree memory, saved-scan size and load time."""
    import gc
    import gzip
    import tempfile
    import time

    from .core import cache
    from .platform.fake import FakePlatform

    out = out or sys.stdout
    parser = argparse.ArgumentParser(prog="somaari-disk bench")
    parser.add_argument("--files", type=int, default=1_000_000)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)

    tree = bench_tree(args.files)
    gc.collect()
    base = _rss()
    t0 = time.monotonic()
    plat = FakePlatform(tree, root=Path("bench"))
    result = Scanner(plat, workers=args.workers).scan(plat.root)
    scan_s = time.monotonic() - t0
    gc.collect()
    tree_mb = (_rss() - base) / 2**20
    del tree, plat
    print(f"scan: {result.files} files, {result.dirs} folders in {scan_s:.1f}s", file=out)
    print(f"scan tree: ~{tree_mb:.0f} MiB RSS growth", file=out)

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "bench.jsonl.gz"
        t0 = time.monotonic()
        with gzip.open(path, "wt", encoding="ascii", compresslevel=1) as fp:
            cache.dump(result, fp, 0)
        save_s = time.monotonic() - t0
        size_mb = path.stat().st_size / 2**20
        del result
        gc.collect()
        t0 = time.monotonic()
        with gzip.open(path, "rt", encoding="ascii") as fp:
            loaded, _ = cache.load(fp)
        load_s = time.monotonic() - t0
    print(f"cache: save {save_s:.1f}s, {size_mb:.1f} MiB, load {load_s:.1f}s", file=out)
    print(f"process RSS now: {_rss() / 2**20:.0f} MiB ({loaded.files} files loaded)", file=out)
    return 0
