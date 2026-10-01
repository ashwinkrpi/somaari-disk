"""OS access layer (linux, windows, fake)."""

import sys

from .base import Platform


def get_platform() -> Platform:
    """Real platform for this machine, chosen by sys.platform."""
    if sys.platform == "win32":
        from .windows import WindowsPlatform

        return WindowsPlatform()
    from .linux import LinuxPlatform  # Linux; other POSIX systems are untested

    return LinuxPlatform()
