"""Platform interface: all OS access goes through here (linux, windows, fake)."""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


class Kind(enum.Enum):
    FILE = "file"
    DIR = "dir"
    LINK = "link"  # symlink, junction or other reparse-point dir: never followed
    OTHER = "other"  # fifo, socket, device


@dataclass(frozen=True, slots=True)
class EntryInfo:
    name: str
    kind: Kind
    size: int = 0  # apparent bytes (0 for dirs and links)
    size_on_disk: int = 0  # allocated bytes
    mtime: int = 0  # UTC epoch seconds
    dev: int | None = None  # filesystem id, None if unknown
    file_id: int | None = None  # set only for files with more than one hard link


@dataclass(slots=True)
class Listing:
    entries: list[EntryInfo] = field(default_factory=list)
    errors: list[tuple[str, OSError]] = field(default_factory=list)  # (name, error) per entry


@dataclass(frozen=True, slots=True)
class Volume:
    """A mounted filesystem. Sizes in bytes."""

    id: str  # stable while mounted: device path, or fs_type:mount_point
    label: str
    mount_point: Path
    fs_type: str
    total: int
    used: int
    free: int
    is_removable: bool = False
    is_network: bool = False
    is_read_only: bool = False
    device_name: str = ""
    is_system: bool = False

    def identity(self) -> tuple:
        """What counts as 'changed' for hot-plug (not free space, which moves constantly)."""
        return (self.id, self.mount_point, self.label, self.fs_type, self.is_read_only)


@dataclass(frozen=True, slots=True)
class Permissions:
    owner: str  # user name, or numeric id if unknown
    group: str  # "" where the OS has no group concept
    mode: str  # display string, e.g. "-rw-r--r--"
    is_read_only: bool  # for the current user


class TrashUnavailableError(PermissionError):
    """The item's volume has no usable Trash (and one can't be created there)."""


class Platform(ABC):
    @abstractmethod
    def stat(self, path: Path) -> EntryInfo:
        """Info for one path without following links. Raises OSError."""

    @abstractmethod
    def scandir(self, path: Path) -> Listing:
        """Entries of a directory, without following links. Raises OSError if unreadable."""

    @abstractmethod
    def list_volumes(self) -> list[Volume]:
        """Mounted user-visible filesystems (internal, USB, SD, NVMe, network). Never raises."""

    @abstractmethod
    def owner_and_perms(self, path: Path) -> Permissions:
        """Owner and permissions of a path, without following links. Raises OSError."""

    def created(self, path: Path) -> int | None:
        """Creation (birth) time in UTC epoch seconds; None if the filesystem doesn't keep it.
        Raises OSError if the path can't be read."""
        return None

    @abstractmethod
    def is_hidden(self, path: Path) -> bool:
        """Hidden by OS convention (dot name, hidden attribute)."""

    @abstractmethod
    def trash(self, path: Path) -> None:
        """Move to the trash of the path's volume. Never deletes permanently. Raises OSError."""

    @abstractmethod
    def reveal(self, path: Path) -> None:
        """Show the path in the OS file manager. Raises OSError if it cannot be launched."""
