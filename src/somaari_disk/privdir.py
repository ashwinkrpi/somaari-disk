"""Owner-only folders for data that lists real file names (scan caches, logs)."""

from __future__ import annotations

import os
from pathlib import Path

PRIVATE_MODE = 0o700


def make_private_dir(path: Path) -> None:
    """mkdir -p, then restrict path to its owner on POSIX (also when it already existed).

    Windows per-user AppData folders are already private via ACLs, so it is left alone.
    Raises OSError only if the folder can't be created; a failed chmod is ignored.
    """
    path.mkdir(parents=True, exist_ok=True, mode=PRIVATE_MODE)
    if os.name != "posix":
        return
    try:
        if path.stat().st_mode & 0o777 != PRIVATE_MODE:
            path.chmod(PRIVATE_MODE)
    except OSError:
        pass  # e.g. owned by another user: still usable, just not tightened
