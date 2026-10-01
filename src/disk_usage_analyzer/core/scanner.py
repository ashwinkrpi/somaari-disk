"""Directory scanner: iterative, thread-pooled listing, cancellable, never follows links.

Worker threads only list directories (I/O). The calling thread builds the tree, so the
tree and hard-link set need no locks.
"""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path

from ..platform.base import Kind, Listing, Platform
from .models import Node, ScanProgress, ScanResult, ScanWarning
from .tree import path_segments

ProgressCallback = Callable[[ScanProgress], None]


def _warning(err: OSError, segments: tuple[str, ...]) -> ScanWarning:
    if isinstance(err, PermissionError):
        kind = "permission"
    elif isinstance(err, FileNotFoundError | NotADirectoryError):
        kind = "vanished"
    else:
        kind = "error"
    return ScanWarning(kind, segments, err.strerror or str(err))


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

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def scan(self, root: Path) -> ScanResult:
        start = time.monotonic()
        result = ScanResult(Node(root.name or str(root)), root)
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
        dirs_in_order = [root_node]  # parents always before their children
        total = 0
        last_report = start

        pool = ThreadPoolExecutor(self._workers, thread_name_prefix="scan")
        pending: dict[Future[Listing], tuple[Node, Path]] = {
            pool.submit(self._platform.scandir, root): (root_node, root)
        }
        try:
            while pending:
                if self._cancel.is_set():
                    result.cancelled = True
                    break
                done, _ = wait(pending, timeout=self._interval, return_when=FIRST_COMPLETED)
                for fut in done:
                    node, path = pending.pop(fut)
                    try:
                        listing = fut.result()
                    except OSError as e:
                        result.warnings.append(_warning(e, path_segments(node)))
                        continue
                    base = path_segments(node) if listing.errors else ()
                    for name, err in listing.errors:
                        result.warnings.append(_warning(err, (*base, name)))
                    children = node.children
                    assert children is not None
                    for e in listing.entries:
                        if e.kind is Kind.LINK:
                            result.links_skipped += 1
                            continue
                        name = sys.intern(e.name)
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
                                child_path = path / name
                                fut2 = pool.submit(self._platform.scandir, child_path)
                                pending[fut2] = (child, child_path)
                                dirs_in_order.append(child)
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
                        children.append(child)
                now = time.monotonic()
                if self._progress and now - last_report >= self._interval:
                    last_report = now
                    self._report(result, total, now - start)
        finally:
            pool.shutdown(wait=not result.cancelled, cancel_futures=True)

        for d in reversed(dirs_in_order):  # children are summed before their parents
            for c in d.children or ():
                d.size += c.size
                d.size_on_disk += c.size_on_disk
        result.elapsed = time.monotonic() - start
        if self._progress:
            self._report(result, total, result.elapsed)
        return result

    def _report(self, result: ScanResult, total: int, elapsed: float) -> None:
        assert self._progress is not None
        self._progress(
            ScanProgress(result.files, result.dirs, total, len(result.warnings), elapsed)
        )
