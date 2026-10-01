"""Scan data models. Sizes are int bytes, times are UTC epoch seconds."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


class Node:
    """One file or folder. Slots keep the tree compact; dirs have a children list."""

    __slots__ = ("name", "is_dir", "size", "size_on_disk", "mtime", "parent", "children")

    def __init__(
        self,
        name: str,
        is_dir: bool = False,
        size: int = 0,
        size_on_disk: int = 0,
        mtime: int = 0,
        parent: Node | None = None,
    ):
        self.name = name
        self.is_dir = is_dir
        self.size = size
        self.size_on_disk = size_on_disk
        self.mtime = mtime
        self.parent = parent
        self.children: list[Node] | None = [] if is_dir else None

    @property
    def child_count(self) -> int:
        return len(self.children) if self.children else 0

    def __repr__(self) -> str:
        return f"Node({self.name!r}, dir={self.is_dir}, size={self.size})"


@dataclass(frozen=True, slots=True)
class ScanWarning:
    kind: str  # "permission" | "vanished" | "error" | "other_filesystem"
    path: tuple[str, ...]  # segments relative to the scan root
    message: str = ""


@dataclass(frozen=True, slots=True)
class ScanProgress:
    files: int
    dirs: int
    size: int
    warnings: int
    elapsed: float  # seconds


@dataclass(frozen=True, slots=True)
class RollUp:
    """The "Other (N items)" tail of a folder's children."""

    count: int
    size: int
    size_on_disk: int


@dataclass(slots=True)
class ScanResult:
    root: Node
    root_path: Path
    warnings: list[ScanWarning] = field(default_factory=list)
    cancelled: bool = False
    files: int = 0
    dirs: int = 0
    links_skipped: int = 0
    hardlinks_deduped: int = 0
    elapsed: float = 0.0
