"""Rotating app log in the config dir. Home paths are replaced with "~" in every line."""

from __future__ import annotations

import logging
import logging.handlers
import platform
import re
import sys
from pathlib import Path, PurePath, PureWindowsPath

MAX_BYTES = 512 * 1024
BACKUPS = 3  # app.log + 3 old files: at most 2 MiB on disk
LOG_NAME = "app.log"


class RedactingFormatter(logging.Formatter):
    """Formats as usual, then hides the home folder (also inside tracebacks)."""

    def __init__(self, home: PurePath, fmt: str | None = None):
        super().__init__(fmt or "%(asctime)s %(levelname)s %(name)s: %(message)s")
        forms = {str(home), home.as_posix()}
        # repr() of a Windows path (exception args, tracebacks) doubles every backslash
        forms |= {f.replace("\\", "\\\\") for f in forms}
        homes = sorted((f for f in forms if len(f) > 1), key=len, reverse=True)  # never "/" or ""
        # Windows paths are case-insensitive: C:\Users\X and c:\users\x are the same folder
        flags = re.IGNORECASE if isinstance(home, PureWindowsPath) else 0
        self._pattern = re.compile("|".join(map(re.escape, homes)), flags) if homes else None

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        return self._pattern.sub("~", text) if self._pattern else text


def setup_logging(log_dir: Path, home: Path, level: int = logging.INFO) -> Path | None:
    """Log the package to log_dir/app.log. Returns the log path, or None if not writable."""
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            log_dir / LOG_NAME,
            maxBytes=MAX_BYTES,
            backupCount=BACKUPS,
            encoding="utf-8",
            errors="backslashreplace",  # non-UTF-8 names must never break logging
        )
    except OSError:
        return None  # read-only config dir: run without a log
    handler.setFormatter(RedactingFormatter(home))
    logger = logging.getLogger(__package__)
    for old in [h for h in logger.handlers if isinstance(h, logging.FileHandler)]:
        logger.removeHandler(old)
        old.close()
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.info(
        "start: python %s, %s %s",
        platform.python_version(),
        sys.platform,
        platform.machine(),
    )
    return log_dir / LOG_NAME
