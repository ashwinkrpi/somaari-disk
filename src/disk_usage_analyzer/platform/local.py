"""os.scandir-based platform shared by Linux and Windows until Stage 2/7 split it."""

from __future__ import annotations

import os
import stat as st
from pathlib import Path

from .base import EntryInfo, Kind, Listing, Platform

_REPARSE_POINT = getattr(st, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _info(name: str, s: os.stat_result) -> EntryInfo:
    mode = s.st_mode
    attrs = getattr(s, "st_file_attributes", 0)
    if st.S_ISLNK(mode) or (st.S_ISDIR(mode) and attrs & _REPARSE_POINT):
        return EntryInfo(name, Kind.LINK, mtime=int(s.st_mtime))
    if st.S_ISDIR(mode):
        kind, size = Kind.DIR, 0
    else:
        kind = Kind.FILE if st.S_ISREG(mode) else Kind.OTHER
        size = s.st_size
    blocks = getattr(s, "st_blocks", None)
    on_disk = blocks * 512 if blocks is not None else size  # Windows: refined in Stage 7
    file_id = s.st_ino if kind is not Kind.DIR and s.st_nlink > 1 and s.st_ino else None
    return EntryInfo(name, kind, size, on_disk, int(s.st_mtime), s.st_dev or None, file_id)


class LocalPlatform(Platform):
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
