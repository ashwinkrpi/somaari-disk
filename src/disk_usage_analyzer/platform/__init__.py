"""OS access layer (linux, windows, fake)."""

from .base import Platform


def get_platform() -> Platform:
    """Real platform for this machine. Stage 2 chooses linux/windows by sys.platform."""
    from .local import LocalPlatform

    return LocalPlatform()
