"""Last scan per location, kept as gzip JSON Lines in the cache dir (see core/cache.py)."""

from __future__ import annotations

import gzip
import hashlib
import logging
import threading
import time
import zlib
from pathlib import Path

from ..core import cache
from ..core.models import ScanResult
from ..privdir import make_private_dir

log = logging.getLogger(__name__)
SUFFIX = ".jsonl.gz"
KEEP = 8  # saved scans kept; older ones are pruned (each can be tens of MB)


class ScanStore:
    def __init__(self, directory: Path, keep: int = KEEP):
        self.directory = directory
        self._keep = keep
        self._lock = threading.Lock()  # one writer at a time; last save wins

    def path_for(self, location: str) -> Path:
        """Hashed name: volume ids may hold characters not allowed in file names."""
        digest = hashlib.sha256(location.encode("utf-8", "surrogatepass")).hexdigest()[:24]
        return self.directory / f"{digest}{SUFFIX}"

    def has(self, location: str) -> bool:
        return self.path_for(location).is_file()

    def save(self, location: str, result: ScanResult, scanned_at: int) -> bool:
        """Write atomically (temp file + rename). Never raises; False if it failed."""
        path = self.path_for(location)
        tmp = path.with_name(path.name + ".tmp")
        start = time.monotonic()
        with self._lock:
            try:
                make_private_dir(self.directory)  # caches list every scanned name
                with gzip.open(tmp, "wt", encoding="ascii", compresslevel=1) as fp:
                    cache.dump(result, fp, scanned_at)
                tmp.replace(path)
                size = path.stat().st_size
            except (OSError, ValueError) as e:
                log.warning("cache save failed: %s", type(e).__name__)
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass  # the folder itself is the problem
                return False
            self._prune(keep=path)
        log.info(
            "cache saved: %d nodes in %.2fs, %d bytes",
            result.files + result.dirs,
            time.monotonic() - start,
            size,
        )
        return True

    def load(self, location: str) -> tuple[ScanResult, int] | None:
        """The saved scan and its UTC time, or None. A broken file is deleted."""
        path = self.path_for(location)
        start = time.monotonic()
        try:
            with gzip.open(path, "rt", encoding="ascii") as fp:
                result, scanned_at = cache.load(fp)
        except FileNotFoundError:
            return None
        except (OSError, EOFError, zlib.error, ValueError) as e:  # CacheError is a ValueError
            log.warning("cache unreadable, removed: %s", type(e).__name__)
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            return None
        log.info(
            "cache loaded: %d nodes in %.2fs", result.files + result.dirs, time.monotonic() - start
        )
        return result, scanned_at

    def _prune(self, keep: Path) -> None:
        try:
            files = sorted(
                (p for p in self.directory.iterdir() if p.name.endswith(SUFFIX) and p != keep),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for p in files[self._keep - 1 :]:
                p.unlink(missing_ok=True)
        except OSError:
            pass  # pruning is best effort
