"""Platform interface. Stage 1 holds only what the scanner needs; Stage 2 extends it."""

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


class Platform(ABC):
    @abstractmethod
    def stat(self, path: Path) -> EntryInfo:
        """Info for one path without following links. Raises OSError."""

    @abstractmethod
    def scandir(self, path: Path) -> Listing:
        """Entries of a directory, without following links. Raises OSError if unreadable."""
