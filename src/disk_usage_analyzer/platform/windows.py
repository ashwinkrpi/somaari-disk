"""Windows platform. Stubs until Stage 7; see docs/PLATFORM.md for what each needs."""

from __future__ import annotations

import os
import stat as st
from pathlib import Path

from .base import Permissions, Volume
from .local import LocalPlatform

_HIDDEN = getattr(st, "FILE_ATTRIBUTE_HIDDEN", 0x2)


class WindowsPlatform(LocalPlatform):
    def list_volumes(self) -> list[Volume]:
        raise NotImplementedError("Windows volumes: Stage 7 (GetLogicalDrives, GetDriveType)")

    def owner_and_perms(self, path: Path) -> Permissions:
        raise NotImplementedError("Windows owner/permissions: Stage 7")

    def is_hidden(self, path: Path) -> bool:
        try:
            attrs = getattr(os.lstat(path), "st_file_attributes", 0)
        except OSError:
            attrs = 0
        return bool(attrs & _HIDDEN) or path.name.startswith(".")

    def reveal(self, path: Path) -> None:
        raise NotImplementedError("Windows reveal: Stage 7 (explorer /select,<path>)")
