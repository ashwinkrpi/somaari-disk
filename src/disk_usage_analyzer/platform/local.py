"""os.scandir-based base shared by LinuxPlatform and WindowsPlatform."""

from __future__ import annotations

import errno
import os
import stat as st
from pathlib import Path

from .base import EntryInfo, Kind, Listing, Platform, TrashUnavailableError

_REPARSE_POINT = getattr(st, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _info(name: str, s: os.stat_result, on_disk: int | None = None) -> EntryInfo:
    mode = s.st_mode
    attrs = getattr(s, "st_file_attributes", 0)
    if st.S_ISLNK(mode) or (st.S_ISDIR(mode) and attrs & _REPARSE_POINT):
        return EntryInfo(name, Kind.LINK, mtime=int(s.st_mtime))
    if st.S_ISDIR(mode):
        kind, size = Kind.DIR, 0
    else:
        kind = Kind.FILE if st.S_ISREG(mode) else Kind.OTHER
        size = s.st_size
    if on_disk is None:
        blocks = getattr(s, "st_blocks", None)  # POSIX; Windows passes on_disk instead
        on_disk = blocks * 512 if blocks is not None else size
    file_id = s.st_ino if kind is not Kind.DIR and s.st_nlink > 1 and s.st_ino else None
    return EntryInfo(name, kind, size, on_disk, int(s.st_mtime), s.st_dev or None, file_id)


class LocalPlatform(Platform):
    """Real-filesystem parts that are the same on every OS. Subclasses add the rest."""

    def stat(self, path: Path) -> EntryInfo:
        return _info(path.name or str(path), os.lstat(path))

    def scandir(self, path: Path) -> Listing:
        listing = Listing()
        with os.scandir(path) as it:
            for entry in it:
                try:
                    listing.entries.append(_info(entry.name, entry.stat(follow_symlinks=False)))
                except OSError as e:
                    listing.errors.append((entry.name, e))
        return listing

    def created(self, path: Path) -> int | None:
        birth = getattr(os.lstat(path), "st_birthtime", None)  # Windows, macOS; not Linux
        return int(birth) if birth else None

    def trash(self, path: Path) -> None:
        if not os.path.lexists(path):
            raise FileNotFoundError(errno.ENOENT, "not found", str(path))
        if path == Path(path.anchor) or os.path.ismount(path):
            raise PermissionError(errno.EPERM, "cannot trash a volume root", str(path))
        if not os.access(path.parent, os.W_OK):
            raise PermissionError(errno.EACCES, "folder is read-only", str(path))
        from send2trash import send2trash  # lazy: only on user action

        try:
            send2trash(path)  # trash of the item's own volume (.Trash-<uid> on USB, Recycle Bin)
        except OSError as e:
            # send2trash: no writable .Trash-<uid> at the volume top and none in the home trash
            if type(e).__name__ == "TrashPermissionError":
                msg = "no trash on this drive"
                raise TrashUnavailableError(errno.EACCES, msg, str(path)) from e
            raise
        except Exception as e:  # send2trash raises plain Exception on some failures
            raise OSError(errno.EIO, str(e), str(path)) from e
