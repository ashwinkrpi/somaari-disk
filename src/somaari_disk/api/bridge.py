"""JS bridge: every public method is callable from the UI and returns a plain JSON-safe dict.

Paths travel as segment lists relative to the scan root. Long work (scans, hot-plug) runs
in threads and reports through `emit(event, payload)`: scan_progress, scan_done,
volumes_changed, error. Failures return {"ok": False, "error": {"code", "message"}}.
"""

from __future__ import annotations

import errno
import functools
import inspect
import itertools
import json
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..core.models import Node, ScanProgress, ScanResult, Volume
from ..core.scanner import Scanner, device_gone
from ..core.tree import SORT_KEYS, find_node, path_segments, remove_node, sorted_children
from ..core.volumes import VolumeWatcher
from ..platform.base import Platform, TrashUnavailableError
from .scan_store import ScanStore

log = logging.getLogger(__name__)

Emit = Callable[[str, dict], None]
HOME_ID = "home"
THEMES = ("system", "light", "dark")
DEFAULT_PREFS: dict[str, Any] = {"theme": "system"}
MAX_CHILDREN = 500
# scan warning kind -> "problem" flag on an item: why its size may be incomplete
PROBLEMS = {
    "permission": "denied",
    "error": "unreadable",
    "vanished": "unreadable",
    "other_filesystem": "other_drive",
}
# get_usage "level" by percent used: < 50, 50 to < 75, 75 to < 100, full
USAGE_LEVELS = ((50.0, "ok"), (75.0, "filling"), (100.0, "low"))


def usage_level(used: int, total: int, free: int) -> str:
    """ "ok" | "filling" | "low" | "full". Full = no free space left (reserved blocks aside)."""
    if total <= 0:
        return "ok"  # size unknown (some network/virtual mounts): nothing to warn about
    percent = _share(used, total) * 100
    if free <= 0 or percent >= 100:
        return "full"
    return next(level for limit, level in USAGE_LEVELS if percent < limit)


class ApiError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _fail(code: str, message: str) -> dict:
    return {"ok": False, "error": {"code": code, "message": message}}


def _guard(fn):
    """Turn ApiError/OSError into an error dict; JS never sees a Python exception."""

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        try:
            return fn(self, *args, **kwargs)
        except ApiError as e:
            return _fail(e.code, e.message)
        except OSError as e:
            return _os_error(e)
        except Exception:  # a bug: log it, and keep the UI alive with a plain message
            log.exception("api %s failed", fn.__name__)
            return _fail("internal", "Something went wrong. Details are in the app log.")

    # pywebview builds the JS stubs from getfullargspec, which reads __signature__ only
    wrapper.__signature__ = inspect.signature(fn)  # type: ignore[attr-defined]
    return wrapper


READ_ONLY_MSG = "This drive is read-only, so nothing on it can be changed."
HOLDS_DRIVE_MSG = (
    "This folder contains another drive, so it can't be moved to the Trash. "
    "Open it and move the items inside instead."
)
# move_to_trash failures in plain words; read_only and drive_unavailable keep the general text
TRASH_MESSAGES = {
    "permission": "You don't have permission to move this item. Nothing was moved.",
    "no_trash": "This drive has no Trash and one can't be made on it, so the item can't be "
    "moved to the Trash. Nothing was deleted.",
    "not_found": "This item is already gone (moved or deleted outside this app). "
    "The list has been updated.",
}


def _os_error(e: OSError) -> dict:
    if e.errno == errno.EROFS:
        return _fail("read_only", READ_ONLY_MSG)
    if device_gone(e):
        return _fail("drive_unavailable", "The drive can't be reached. It may have been unplugged.")
    if isinstance(e, TrashUnavailableError):
        return _fail("no_trash", TRASH_MESSAGES["no_trash"])
    if isinstance(e, PermissionError):
        return _fail("permission", "You don't have permission to do that here.")
    if isinstance(e, FileNotFoundError):
        return _fail("not_found", "This item no longer exists. Rescan to refresh the list.")
    return _fail("os_error", e.strerror or str(e))


def volume_dict(v: Volume) -> dict:
    return {
        "id": v.id,
        "label": v.label,
        "mount_point": str(v.mount_point),
        "mount_segments": list(v.mount_point.parts),
        "fs_type": v.fs_type,
        "total": v.total,
        "used": v.used,
        "free": v.free,
        "is_removable": v.is_removable,
        "is_network": v.is_network,
        "is_read_only": v.is_read_only,
        "device_name": v.device_name,
        "is_system": v.is_system,
    }


def _share(part: int, whole: int) -> float:
    return part / whole if whole > 0 else 0.0


def _location_kind(location: str) -> str:
    """Location for the log: never a path (home folder paths name the user)."""
    return "home" if location == HOME_ID else "volume"


def _volume_for(path: Path, volumes: list[Volume]) -> Volume | None:
    """Volume whose mount point is the deepest ancestor of path."""
    best: Volume | None = None
    for v in volumes:
        if path == v.mount_point or v.mount_point in path.parents:
            if best is None or len(v.mount_point.parts) > len(best.mount_point.parts):
                best = v
    return best


class Api:
    def __init__(
        self,
        platform: Platform,
        emit: Emit | None = None,
        *,
        home: Path | None = None,
        prefs_path: Path | None = None,
        cache_dir: Path | None = None,
        about: Callable[[], dict] | None = None,
        volume_interval: float = 3.0,
        progress_interval: float = 0.25,
        workers: int = 4,
    ):
        self._platform = platform
        self._emit_fn = emit
        self._home = home
        self._prefs_path = prefs_path
        self._about = about
        self._progress_interval = progress_interval
        self._workers = workers
        self._lock = threading.Lock()  # guards scan state and the tree
        self._scan_ids = itertools.count(1)
        self._scan_id = 0
        self._scanner: Scanner | None = None
        self._thread: threading.Thread | None = None
        self._result: ScanResult | None = None
        self._location: str | None = None
        self._read_only = False  # the scanned location's drive is mounted read-only
        self._scanned_at: int | None = None  # UTC time of the scan shown (cached or new)
        self._store = ScanStore(cache_dir) if cache_dir is not None else None
        self._watcher = VolumeWatcher(platform, self._on_volumes, volume_interval)

    # ---- events -------------------------------------------------------------

    def set_emitter(self, emit: Emit) -> None:
        self._emit_fn = emit

    def _emit(self, event: str, payload: dict) -> None:
        if self._emit_fn is None:
            return
        try:
            self._emit_fn(event, payload)
        except Exception:
            pass  # the window may be closing; never kill a worker thread over it

    def _on_volumes(self, vols: list[Volume]) -> None:
        self._emit("volumes_changed", {"volumes": [volume_dict(v) for v in vols]})

    def set_visible(self, visible: bool) -> dict:
        """Window shown/hidden: hot-plug polling only runs while visible."""
        if visible:
            self._watcher.resume()
        else:
            self._watcher.pause()
        return {"ok": True}

    def shutdown(self) -> dict:
        self.cancel_scan()
        self._watcher.stop()
        return {"ok": True}

    @_guard
    def get_about(self) -> dict:
        """About section: debug info, field labels, developer info, "Copy debug info" text."""
        if self._about is None:
            raise ApiError("unavailable", "About information isn't available.")
        return {"ok": True, **self._about()}

    # ---- locations ----------------------------------------------------------

    def list_volumes(self) -> dict:
        vols = self._watcher.snapshot()
        home = None
        if self._home is not None:
            home = {"id": HOME_ID, "label": "Home folder", "path": str(self._home)}
        return {"ok": True, "volumes": [volume_dict(v) for v in vols], "home": home}

    def _resolve(self, location: str) -> Path:
        if location == HOME_ID and self._home is not None:
            return self._home
        for v in self._platform.list_volumes():
            if v.id == location:
                return v.mount_point
        raise ApiError("unknown_location", "That drive is no longer connected.")

    # ---- scanning -----------------------------------------------------------

    @_guard
    def scan(self, location: str, use_cache: bool = False) -> dict:
        """Start scanning a location (volume id or "home"); cancels any running scan.
        use_cache=True shows the last saved scan of that location instead, if there is one."""
        root = self._resolve(location)
        vol = _volume_for(root, self._platform.list_volumes())  # once per scan, not per click
        with self._lock:
            if self._scanner is not None:
                self._scanner.cancel()
            self._read_only = vol is not None and vol.is_read_only
            scan_id = next(self._scan_ids)
            scanner = Scanner(
                self._platform,
                workers=self._workers,
                progress=lambda p: self._on_progress(scan_id, p),
                progress_interval=self._progress_interval,
            )
            self._scan_id, self._scanner = scan_id, scanner
            self._result, self._location, self._scanned_at = None, location, None
            cached = bool(use_cache) and self._store is not None and self._store.has(location)
            self._thread = threading.Thread(
                target=self._run_scan,
                args=(scan_id, scanner, root, location, cached),
                daemon=True,
                name="scan",
            )
            self._thread.start()
        log.info("scan %d start: %s%s", scan_id, _location_kind(location), " (cached)" * cached)
        return {"ok": True, "scan_id": scan_id, "location": location, "cached": cached}

    def cancel_scan(self) -> dict:
        with self._lock:
            if self._scanner is not None:
                self._scanner.cancel()
        return {"ok": True}

    def _on_progress(self, scan_id: int, p: ScanProgress) -> None:
        if scan_id != self._scan_id:
            return
        self._emit(
            "scan_progress",
            {
                "scan_id": scan_id,
                "files": p.files,
                "dirs": p.dirs,
                "size": p.size,
                "warnings": p.warnings,
                "elapsed": p.elapsed,
            },
        )

    def _run_scan(
        self, scan_id: int, scanner: Scanner, root: Path, location: str, use_cache: bool
    ) -> None:
        loaded = None
        try:
            if use_cache and self._store is not None:
                loaded = self._store.load(location)
            if loaded is not None:
                result, scanned_at = loaded
                result.root_path = root  # same drive may mount elsewhere after a replug
            else:
                result, scanned_at = scanner.scan(root), int(time.time())
        except Exception as e:  # a scan bug must surface in the UI, not kill silently
            log.exception("scan %d crashed", scan_id)
            self._emit("error", {"scan_id": scan_id, "code": "scan_failed", "message": str(e)})
            return
        with self._lock:
            if scan_id != self._scan_id:
                return  # superseded by a newer scan
            self._scanner = None
            failed = not result.root.is_dir and result.files == 0
            if not failed:
                self._result, self._scanned_at = result, scanned_at
        log.info(
            "scan %d done: files=%d dirs=%d %.2fs cancelled=%s lost=%s warnings=%s cached=%s",
            scan_id,
            result.files,
            result.dirs,
            result.elapsed,
            result.cancelled,
            result.volume_lost,
            self._warning_counts(result),
            loaded is not None,
        )
        payload: dict[str, Any] = {
            "scan_id": scan_id,
            "ok": not failed,
            "cached": loaded is not None,
            "scanned_at": scanned_at,
            "cancelled": result.cancelled,
            "volume_lost": result.volume_lost,
            "files": result.files,
            "dirs": result.dirs,
            "size": result.root.size,
            "size_on_disk": result.root.size_on_disk,
            "elapsed": result.elapsed,
            "warnings": self._warning_counts(result),
            "breadcrumb": self._breadcrumb(result, ()),
        }
        if failed:
            msg = result.warnings[0].message if result.warnings else "not a folder"
            payload["error"] = {"code": "scan_failed", "message": f"Cannot scan: {msg}"}
        self._emit("scan_done", payload)
        complete = not (failed or result.cancelled or result.volume_lost)
        if loaded is None and complete:
            self._save(location, result, scanned_at)

    def _save(self, location: str, result: ScanResult, scanned_at: int) -> None:
        """Save the scan for next start, unless a newer scan replaced it meanwhile."""
        if self._store is not None and self._result is result:
            self._store.save(location, result, scanned_at)

    @staticmethod
    def _warning_counts(result: ScanResult) -> dict[str, int]:
        counts: dict[str, int] = {}
        for w in result.warnings:
            counts[w.kind] = counts.get(w.kind, 0) + 1
        return counts

    # ---- tree queries -------------------------------------------------------

    def _need_result(self, live: bool = False) -> ScanResult:
        """The finished scan, or with live=True the partial tree of the running scan."""
        if self._result is not None:
            return self._result
        if live and self._scanner is not None:
            partial = self._scanner.result
            if partial is not None and partial.root.is_dir:
                return partial
        raise ApiError("no_scan", "Nothing has been scanned yet.")

    def _need_node(self, segments: list[str], live: bool = False) -> tuple[ScanResult, Node]:
        result = self._need_result(live)
        node = find_node(result.root, tuple(segments or ()))
        if node is None:
            raise ApiError("not_found", "This item is not in the scan. Rescan to refresh.")
        return result, node

    @staticmethod
    def _breadcrumb(result: ScanResult, segments: tuple[str, ...]) -> list[dict]:
        """Root path parts (not navigable) + scan root + each segment below it."""
        parts = result.root_path.parts
        crumbs: list[dict] = [{"name": p, "segments": None} for p in parts[:-1]]
        crumbs.append({"name": parts[-1] if parts else result.root.name, "segments": []})
        crumbs += [{"name": s, "segments": list(segments[: i + 1])} for i, s in enumerate(segments)]
        return crumbs

    @staticmethod
    def _node_dict(node: Node, by: str = "size") -> dict:
        parent = node.parent
        return {
            "name": node.name,
            "segments": list(path_segments(node)),
            "is_dir": node.is_dir,
            "size": node.size,
            "size_on_disk": node.size_on_disk,
            "mtime": node.mtime,
            "child_count": node.child_count,
            "share_parent": _share(getattr(node, by), getattr(parent, by)) if parent else 1.0,
        }

    @staticmethod
    def _problems(result: ScanResult) -> dict[tuple[str, ...], str]:
        """Segments -> why that item could not be read fully (see PROBLEMS)."""
        # list() copies first: a running scan may append warnings meanwhile
        return {w.path: PROBLEMS[w.kind] for w in list(result.warnings) if w.kind in PROBLEMS}

    def _flagged(self, node: Node, by: str, problems: dict[tuple[str, ...], str]) -> dict:
        d = self._node_dict(node, by)
        d["problem"] = problems.get(tuple(d["segments"]))
        return d

    @_guard
    def get_children(
        self, segments: list[str] | None = None, sort: str = "size", limit: int = 100, on_disk=False
    ) -> dict:
        """Largest `limit` children of a folder in `sort` order, plus an "Other" roll-up."""
        if sort not in SORT_KEYS:
            raise ApiError("bad_request", f"Unknown sort order: {sort}")
        limit = max(1, min(int(limit), MAX_CHILDREN))
        by = "size_on_disk" if on_disk else "size"
        with self._lock:
            result, node = self._need_node(segments or [], live=True)
            if not node.is_dir:
                raise ApiError("not_a_folder", "This item is a file, not a folder.")
            top, rest = sorted_children(node, limit, sort, by)
            problems = self._problems(result)
            return {
                "ok": True,
                "partial": result is not self._result,
                "node": self._flagged(node, by, problems),
                "breadcrumb": self._breadcrumb(result, path_segments(node)),
                "is_root": node.parent is None,
                "children": [self._flagged(c, by, problems) for c in top],
                "rest": None
                if rest is None
                else {"count": rest.count, "size": rest.size, "size_on_disk": rest.size_on_disk},
            }

    @_guard
    def get_details(self, segments: list[str]) -> dict:
        """Everything the detail panel shows for one item."""
        with self._lock:
            result, node = self._need_node(segments, live=True)
            details = self._flagged(node, "size", self._problems(result))
            path = result.root_path.joinpath(*details["segments"])
            details["share_root"] = _share(node.size, result.root.size)
            details["read_only_drive"] = self._read_only
            details["trash_blocked"] = self._trash_blocked(result, node)
        details["ok"] = True
        details["path"] = str(path)
        details["extension"] = "" if node.is_dir else path.suffix.lstrip(".").lower()
        details["hidden"] = self._platform.is_hidden(path)
        try:
            perms = self._platform.owner_and_perms(path)
            details.update(
                owner=perms.owner, group=perms.group, mode=perms.mode, read_only=perms.is_read_only
            )
        except OSError:
            details.update(owner=None, group=None, mode=None, read_only=None)
        try:
            details["created"] = self._platform.created(path)
        except OSError:
            details["created"] = None
        return details

    def _trash_blocked(self, result: ScanResult, node: Node) -> str | None:
        """Why Move to Trash is unavailable for node, in plain words; None if it is allowed."""
        if node.parent is None:
            return "The scanned folder itself can't be moved to the Trash."
        if result is not self._result:
            return "Wait for the scan to finish before moving items to the Trash."
        if self._read_only:
            return READ_ONLY_MSG
        if node.is_dir and self._holds_other_drive(result, path_segments(node)):
            return HOLDS_DRIVE_MSG
        return None

    @staticmethod
    def _holds_other_drive(result: ScanResult, segments: tuple[str, ...]) -> bool:
        """True if the folder is, or contains, another drive's mount point (emptying the
        Trash would then wipe that drive)."""
        n = len(segments)
        return any(
            w.kind == "other_filesystem" and w.path[:n] == segments for w in list(result.warnings)
        )

    @_guard
    def get_usage(self, location: str | None = None) -> dict:
        """Used/free of the volume holding `location` (default: the current scan).
        whole_volume: the location is the drive itself (False for a folder such as Home).
        scanned_size: size of the finished scan shown for this location, else None."""
        if location is None:
            location = self._location
        if location is None:
            raise ApiError("no_scan", "Nothing has been scanned yet.")
        root = self._resolve(location)
        vol = _volume_for(root, self._platform.list_volumes())
        if vol is None:
            raise ApiError("unknown_location", "Can't find the drive for this location.")
        with self._lock:
            shown = self._result if location == self._location else None
            scanned_size = shown.root.size if shown is not None else None
        return {
            "ok": True,
            "volume_id": vol.id,
            "label": vol.label,
            "total": vol.total,
            "used": vol.used,
            "free": vol.free,
            "percent": _share(vol.used, vol.total) * 100,
            "level": usage_level(vol.used, vol.total, vol.free),
            "is_read_only": vol.is_read_only,
            "whole_volume": root == vol.mount_point,
            "scanned_size": scanned_size,
        }

    # ---- actions ------------------------------------------------------------

    @_guard
    def open_in_file_manager(self, segments: list[str]) -> dict:
        with self._lock:
            result, node = self._need_node(segments)
            path = result.root_path.joinpath(*path_segments(node))
        self._platform.reveal(path)
        return {"ok": True}

    @_guard
    def move_to_trash(self, segments: list[str], confirmed: bool = False) -> dict:
        """Move one item to the Trash (never deletes). The UI must confirm first."""
        if confirmed is not True:
            raise ApiError("not_confirmed", "Moving to the Trash needs confirmation.")
        if not segments:
            raise ApiError("bad_request", "The scanned folder itself can't be moved to the Trash.")
        with self._lock:
            result, node = self._need_node(segments)
            path = result.root_path.joinpath(*path_segments(node))
            vol = _volume_for(path, self._platform.list_volumes())
            if vol is not None and vol.is_read_only:
                raise ApiError("read_only", READ_ONLY_MSG)
            if node.is_dir and self._holds_other_drive(result, path_segments(node)):
                raise ApiError("holds_drive", HOLDS_DRIVE_MSG)
            parent = node.parent
            assert parent is not None
            try:
                self._platform.trash(path)
            except OSError as e:
                fail = _os_error(e)
                err = fail["error"]
                if err["code"] == "not_found":
                    remove_node(node)  # gone outside the app: keep the totals honest
                    fail["removed"] = {"size": node.size, "size_on_disk": node.size_on_disk}
                    self._save_later()
                if err["code"] == "os_error":
                    err["message"] = f"Couldn't move this item to the Trash: {err['message']}."
                err["message"] = TRASH_MESSAGES.get(err["code"], err["message"])
                log.info("trash failed: %s", err["code"])
                return fail
            remove_node(node)  # totals update in place: no rescan needed
            self._save_later()
            return {
                "ok": True,
                "removed": {"size": node.size, "size_on_disk": node.size_on_disk},
                "parent": self._node_dict(parent),
                "root": self._node_dict(result.root),
            }

    def _save_later(self) -> None:
        """Re-save the shown scan after a change (trash), off the calling thread."""
        location, result, scanned_at = self._location, self._result, self._scanned_at
        if self._store is None or location is None or result is None or scanned_at is None:
            return
        threading.Thread(
            target=self._save, args=(location, result, scanned_at), daemon=True, name="cache"
        ).start()

    # ---- preferences --------------------------------------------------------

    def get_prefs(self) -> dict:
        prefs = dict(DEFAULT_PREFS)
        if self._prefs_path is not None:
            try:
                stored = json.loads(self._prefs_path.read_text(encoding="utf-8"))
                if isinstance(stored, dict) and stored.get("theme") in THEMES:
                    prefs["theme"] = stored["theme"]
            except (OSError, ValueError):
                pass  # missing or corrupt prefs: defaults
        return {"ok": True, **prefs}

    @_guard
    def set_theme(self, theme: str) -> dict:
        """Theme override: "system" follows the OS, "light"/"dark" force one."""
        if theme not in THEMES:
            raise ApiError("bad_request", f"Unknown theme: {theme}")
        if self._prefs_path is not None:
            prefs = self.get_prefs()
            prefs.pop("ok")
            prefs["theme"] = theme
            self._prefs_path.parent.mkdir(parents=True, exist_ok=True)
            self._prefs_path.write_text(json.dumps(prefs), encoding="utf-8")
        return {"ok": True, "theme": theme}
