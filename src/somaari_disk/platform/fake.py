"""In-memory platform for tests and demo mode. Never touches the real filesystem."""

from __future__ import annotations

import errno
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePath

from .base import EntryInfo, Kind, Listing, Permissions, Platform, Volume

BLOCK = 4096


@dataclass
class FakeFile:
    size: int = 0
    size_on_disk: int | None = None  # default: rounded up to BLOCK
    mtime: int = 0
    file_id: int | None = None  # same id = hard links to one file
    stat_error: type[OSError] | None = None  # raised when the entry is stat'ed
    owner: str = "user"
    read_only: bool = False
    created: int | None = None  # birth time; None = filesystem doesn't record it


@dataclass
class FakeDir:
    children: dict[str, FakeFile | FakeDir | FakeLink] = field(default_factory=dict)
    mtime: int = 0
    dev: int | None = None  # None = inherit from parent
    list_error: type[OSError] | None = None  # raised when the dir is listed
    stat_error: type[OSError] | None = None
    owner: str = "user"
    read_only: bool = False  # also blocks trashing its children
    created: int | None = None


@dataclass
class FakeLink:
    target: str = ""


FakeNode = FakeFile | FakeDir | FakeLink


def _err(cls: type[OSError], path: PurePath) -> OSError:
    code = {PermissionError: errno.EACCES, FileNotFoundError: errno.ENOENT}.get(cls, errno.EIO)
    return cls(code, "fake error", str(path))


class FakePlatform(Platform):
    """Records trash/reveal calls instead of acting. `volumes` may be edited to fake hot-plug.

    vanish_after=N: the volume disappears after N directory listings (unplugged mid-scan).
    """

    def __init__(
        self,
        tree: FakeDir,
        root: Path = Path("fake-root"),
        dev: int = 1,
        volumes: list[Volume] | None = None,
        vanish_after: int | None = None,
        unmounted: list[Volume] | None = None,
    ):
        self.root = root
        self.tree = tree
        if tree.dev is None:
            tree.dev = dev
        self.volumes = list(volumes or [])
        self.unmounted = list(unmounted or [])  # mount() moves one into volumes
        self.mount_error: OSError | None = None  # raised by mount() if set
        self.mounted: list[str] = []
        self.trashed: list[Path] = []
        self.revealed: list[Path] = []
        self.trash_error: OSError | None = None  # raised by trash() if set
        self.gone = False
        self._vanish_after = vanish_after
        self._listings = 0
        self._lock = threading.Lock()

    def _lookup(self, path: Path) -> tuple[FakeNode, int]:
        if self.gone:
            raise _err(FileNotFoundError, path)
        node: FakeNode = self.tree
        dev = self.tree.dev or 0
        try:
            parts = path.relative_to(self.root).parts
        except ValueError:
            raise _err(FileNotFoundError, path) from None
        for part in parts:
            if not isinstance(node, FakeDir) or part not in node.children:
                raise _err(FileNotFoundError, path)
            node = node.children[part]
            if isinstance(node, FakeDir) and node.dev is not None:
                dev = node.dev
        return node, dev

    @staticmethod
    def _info(name: str, node: FakeNode, dev: int) -> EntryInfo:
        if isinstance(node, FakeLink):
            return EntryInfo(name, Kind.LINK)
        if isinstance(node, FakeDir):
            return EntryInfo(name, Kind.DIR, 0, BLOCK, node.mtime, node.dev or dev)
        on_disk = node.size_on_disk
        if on_disk is None:
            on_disk = -(-node.size // BLOCK) * BLOCK
        return EntryInfo(name, Kind.FILE, node.size, on_disk, node.mtime, dev, node.file_id)

    def stat(self, path: Path) -> EntryInfo:
        node, dev = self._lookup(path)
        if not isinstance(node, FakeLink) and node.stat_error:
            raise _err(node.stat_error, path)
        return self._info(path.name or str(path), node, dev)

    def scandir(self, path: Path) -> Listing:
        with self._lock:
            self._listings += 1
            if self._vanish_after is not None and self._listings > self._vanish_after:
                self.gone = True
        if self.gone:
            raise _err(OSError, path)  # EIO, as from a yanked USB stick
        node, dev = self._lookup(path)
        if not isinstance(node, FakeDir):
            raise _err(NotADirectoryError, path)
        if node.list_error:
            raise _err(node.list_error, path)
        listing = Listing()
        for name, child in node.children.items():
            if not isinstance(child, FakeLink) and child.stat_error:
                listing.errors.append((name, _err(child.stat_error, path / name)))
            else:
                listing.entries.append(self._info(name, child, dev))
        return listing

    def list_volumes(self) -> list[Volume]:
        return [] if self.gone else list(self.volumes)

    def list_unmounted(self) -> list[Volume]:
        return [] if self.gone else list(self.unmounted)

    def mount(self, device: str) -> Path:
        if self.mount_error is not None:
            raise self.mount_error
        for v in self.unmounted:
            if v.device_name == device:
                self.unmounted.remove(v)
                mount = self.root / "media" / v.label
                self.volumes.append(replace(v, mount_point=mount, is_mounted=True))
                self.mounted.append(device)
                return mount
        raise _err(FileNotFoundError, PurePath(device))

    def owner_and_perms(self, path: Path) -> Permissions:
        node, _ = self._lookup(path)
        if isinstance(node, FakeLink):
            return Permissions("user", "user", "lrwxrwxrwx", False)
        is_dir = isinstance(node, FakeDir)
        if node.read_only:
            mode = "dr-xr-xr-x" if is_dir else "-r--r--r--"
        else:
            mode = "drwxr-xr-x" if is_dir else "-rw-r--r--"
        return Permissions(node.owner, node.owner, mode, node.read_only)

    def created(self, path: Path) -> int | None:
        node, _ = self._lookup(path)
        return None if isinstance(node, FakeLink) else node.created

    def is_hidden(self, path: Path) -> bool:
        return path.name.startswith(".")

    def trash(self, path: Path) -> None:
        if path == self.root:
            raise _err(PermissionError, path)
        self._lookup(path)
        parent, _ = self._lookup(path.parent)
        assert isinstance(parent, FakeDir)
        if self.trash_error is not None:
            raise self.trash_error
        if parent.read_only:
            raise _err(PermissionError, path)
        del parent.children[path.name]
        self.trashed.append(path)

    def reveal(self, path: Path) -> None:
        self._lookup(path)
        self.revealed.append(path)
