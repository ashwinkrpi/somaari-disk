"""Directory scanner: iterative, thread-pooled listing, cancellable, never follows links.

Worker threads only list directories (I/O), a few at a time. The calling thread builds the
tree, so the tree and hard-link set need no locks. Sizes are added to every ancestor as each
folder is listed, so `Scanner.result` can be read (as a partial tree) while the scan runs.
"""

from __future__ import annotations

import errno
import threading
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path

from ..platform.base import Kind, Listing, Platform
from .models import Node, ScanProgress, ScanResult, ScanWarning
from .tree import path_segments

ProgressCallback = Callable[[ScanProgress], None]

# errno values that mean the device itself is gone, not just one folder.
_DEVICE_GONE = frozenset(
    getattr(errno, n)
    for n in ("ENODEV", "ENXIO", "ENOTCONN", "ESTALE", "ESHUTDOWN")
    if hasattr(errno, n)
)


def _warning(err: OSError, segments: tuple[str, ...]) -> ScanWarning:
    if isinstance(err, PermissionError):
        kind = "permission"
    elif isinstance(err, FileNotFoundError | NotADirectoryError):
        kind = "vanished"
    else:
        kind = "error"
    return ScanWarning(kind, segments, err.strerror or str(err))


def _add_up(node: Node | None, size: int, size_on_disk: int) -> None:
    """Add one folder's newly listed sizes to it and every ancestor."""
    if not size and not size_on_disk:
        return
    while node is not None:
        node.size += size
        node.size_on_disk += size_on_disk
        node = node.parent


class Scanner:
    """One scan. Call scan() from a worker thread; cancel() from any thread."""

    def __init__(
        self,
        platform: Platform,
        *,
        workers: int = 4,
        cross_mounts: bool = False,
        progress: ProgressCallback | None = None,
        progress_interval: float = 0.1,
    ):
        self._platform = platform
        self._workers = max(1, workers)
        self._cross_mounts = cross_mounts
        self._progress = progress
        self._interval = progress_interval
        self._cancel = threading.Event()
        self.result: ScanResult | None = None  # live while scanning; sizes always consistent

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def scan(self, root: Path) -> ScanResult:
        start = time.monotonic()
        result = ScanResult(Node(root.name or str(root)), root)
        self.result = result
        try:
            info = self._platform.stat(root)
        except OSError as e:
            result.warnings.append(_warning(e, ()))
            result.elapsed = time.monotonic() - start
            return result
        if info.kind is not Kind.DIR:
            node = result.root
            if info.kind is not Kind.LINK:
                node.size, node.size_on_disk, node.mtime = info.size, info.size_on_disk, info.mtime
                result.files = 1
            else:
                result.links_skipped = 1
            result.elapsed = time.monotonic() - start
            return result

        root_node = Node(result.root.name, True, 0, info.size_on_disk, info.mtime)
        result.root = root_node
        result.dirs = 1
        root_dev = info.dev
        seen_ids: set[tuple[int | None, int]] = set()
        names: dict[str, str] = {}  # one string per distinct name; dropped after the scan
        total = 0
        last_report = start

        pool = ThreadPoolExecutor(self._workers, thread_name_prefix="scan")
        # Folders wait in a depth-first stack; only a few are in the pool at once. Submitting
        # every folder as found kept ~1 KiB per queued Future alive (GBs on big trees).
        todo: list[tuple[Node, Path]] = [(root_node, root)]
        pending: dict[Future[Listing], tuple[Node, Path]] = {}
        in_flight = self._workers * 2
        try:
            while pending or todo:
                while todo and len(pending) < in_flight:
                    item = todo.pop()
                    pending[pool.submit(self._platform.scandir, item[1])] = item
                if self._cancel.is_set():
                    result.cancelled = True
                    break
                if result.volume_lost:
                    break
                done, _ = wait(pending, timeout=self._interval, return_when=FIRST_COMPLETED)
                for fut in done:
                    node, path = pending.pop(fut)
                    try:
                        listing = fut.result()
                    except OSError as e:
                        if not isinstance(e, PermissionError) and self._lost(root, root_dev, e):
                            result.volume_lost = True
                            result.warnings.append(
                                ScanWarning("volume_lost", (), e.strerror or str(e))
                            )
                            break
                        result.warnings.append(_warning(e, path_segments(node)))
                        continue
                    base = path_segments(node) if listing.errors else ()
                    for name, err in listing.errors:
                        result.warnings.append(_warning(err, (*base, name)))
                    children = node.children
                    assert children is not None
                    added = added_on_disk = 0
                    for e in listing.entries:
                        if e.kind is Kind.LINK:
                            result.links_skipped += 1
                            continue
                        name = names.setdefault(e.name, e.name)
                        if e.kind is Kind.DIR:
                            child = Node(name, True, 0, e.size_on_disk, e.mtime, node)
                            result.dirs += 1
                            other_fs = (
                                root_dev is not None and e.dev is not None and e.dev != root_dev
                            )
                            if other_fs and not self._cross_mounts:
                                child.size_on_disk = 0
                                result.warnings.append(
                                    ScanWarning("other_filesystem", path_segments(child))
                                )
                            else:
                                todo.append((child, path / name))
                            added_on_disk += child.size_on_disk
                        else:
                            size, on_disk = e.size, e.size_on_disk
                            if e.file_id is not None:
                                key = (e.dev, e.file_id)
                                if key in seen_ids:
                                    size = on_disk = 0
                                    result.hardlinks_deduped += 1
                                else:
                                    seen_ids.add(key)
                            child = Node(name, False, size, on_disk, e.mtime, node)
                            result.files += 1
                            total += size
                            added += size
                            added_on_disk += on_disk
                        children.append(child)
                    _add_up(node, added, added_on_disk)
                now = time.monotonic()
                if self._progress and now - last_report >= self._interval:
                    last_report = now
                    self._report(result, total, now - start)
        finally:
            # Don't wait on workers that may be stuck on a cancelled scan or a dead device.
            stop_now = result.cancelled or result.volume_lost
            pool.shutdown(wait=not stop_now, cancel_futures=True)

        result.elapsed = time.monotonic() - start
        if self._progress:
            self._report(result, total, result.elapsed)
        return result

    def _lost(self, root: Path, root_dev: int | None, err: OSError) -> bool:
        """True if the scanned volume is gone (unplugged, unmounted), not just one folder."""
        if err.errno in _DEVICE_GONE:
            return True
        try:
            info = self._platform.stat(root)
        except OSError:
            return True
        if info.kind is not Kind.DIR:
            return True
        return root_dev is not None and info.dev is not None and info.dev != root_dev

    def _report(self, result: ScanResult, total: int, elapsed: float) -> None:
        assert self._progress is not None
        self._progress(
            ScanProgress(result.files, result.dirs, total, len(result.warnings), elapsed)
        )
