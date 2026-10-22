"""Linux platform: volumes from psutil (mount table) + lsblk (device facts), xdg-open reveal."""

from __future__ import annotations

import errno
import functools
import json
import os
import stat as st
import struct
import subprocess
import threading
from collections.abc import Callable, Iterable
from pathlib import Path, PurePosixPath
from typing import Protocol

from .base import Permissions, Volume
from .local import LocalPlatform

LSBLK_TIMEOUT = 3.0  # seconds
USAGE_TIMEOUT = 2.0  # network mounts can hang statvfs
LSBLK_COLUMNS = "NAME,PATH,MOUNTPOINT,LABEL,UUID,MODEL,TYPE,TRAN,RM,HOTPLUG,RO,FSTYPE,SIZE"
MOUNT_TIMEOUT = 30.0  # udisks may check the filesystem first
# Block-level formats that hold other volumes, not a filesystem to mount.
NOT_MOUNTABLE = frozenset(
    "swap LVM2_member crypto_LUKS linux_raid_member zfs_member bcache isw_raid_member "
    "ddf_raid_member squashfs".split()
)

# Pseudo, virtual and image filesystems that are not user storage.
SKIP_FS = frozenset(
    "proc sysfs tmpfs devtmpfs devpts squashfs overlay cgroup cgroup2 mqueue debugfs tracefs "
    "securityfs pstore bpf configfs fusectl autofs binfmt_misc hugetlbfs efivarfs ramfs "
    "rpc_pipefs nsfs selinuxfs nfsd fuse.portal fuse.gvfsd-fuse fuse.lxcfs "
    "fuse.snapfuse".split()
)
NETWORK_FS = frozenset(
    "nfs nfs4 cifs smb3 smbfs ncpfs afs ceph glusterfs 9p fuse.sshfs fuse.rclone davfs "
    "fuse.davfs2".split()
)
SYSTEM_TOP = frozenset({"boot", "efi"})  # first path part of system mounts besides the root
FM1 = "org.freedesktop.FileManager1"
FM1_PATH = "/org/freedesktop/FileManager1"  # D-Bus object path, not a filesystem path
REVEAL_TIMEOUT = 2.0  # seconds
# statx(2): struct statx is 256 bytes; stx_btime.tv_sec (s64) sits at byte 80
AT_FDCWD = -100
AT_SYMLINK_NOFOLLOW = 0x100
STATX_BTIME = 0x800
STATX_SIZE = 256
STATX_BTIME_OFFSET = 80


class Partition(Protocol):  # psutil.disk_partitions() item
    device: str
    mountpoint: str
    fstype: str
    opts: str


Usage = tuple[int, int, int]  # total, used, free


def _flag(v: object) -> bool:
    return v is True or v in ("1", 1, "true")  # lsblk < 2.37 prints "0"/"1"


def parse_lsblk(text: str) -> dict[str, dict]:
    """Device facts keyed by device path and by mount point. Disk-level facts are inherited."""
    out: dict[str, dict] = {}

    def walk(devs: list[dict], parent: dict) -> None:
        for d in devs:
            info = {
                "label": d.get("label") or "",
                "uuid": d.get("uuid") or "",  # filesystem's own id: survives a replug
                "model": (d.get("model") or parent.get("model") or "").strip(),
                "tran": d.get("tran") or parent.get("tran") or "",
                "removable": _flag(d.get("rm")) or _flag(d.get("hotplug")) or parent["removable"],
                "ro": _flag(d.get("ro")),
            }
            for key in (d.get("path"), d.get("mountpoint")):
                if key:
                    out[key] = info
            walk(d.get("children") or [], info)

    try:
        data = json.loads(text)
    except ValueError:
        return {}
    if isinstance(data, dict):
        walk(data.get("blockdevices") or [], {"removable": False})
    return out


def _int(v: object) -> int:
    try:
        return max(0, int(v))  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return 0  # lsblk without -b, or a missing size


def unmounted_from_lsblk(text: str) -> list[Volume]:
    """Removable partitions with a mountable filesystem and no mount point, from lsblk JSON."""
    out: list[Volume] = []

    def walk(devs: list[dict], parent: dict) -> None:
        for d in devs:
            removable = (
                _flag(d.get("rm"))
                or _flag(d.get("hotplug"))
                or (d.get("tran") or parent["tran"]) == "usb"
                or parent["removable"]
            )
            model = (d.get("model") or parent["model"] or "").strip()
            kids = d.get("children") or []
            fs = d.get("fstype") or ""
            path = d.get("path") or ""
            if (
                not kids
                and removable
                and fs
                and fs not in NOT_MOUNTABLE
                and path
                and not d.get("mountpoint")
                and not any(d.get("mountpoints") or [])
                and d.get("type") in ("part", "disk")
            ):
                out.append(
                    Volume(
                        id=f"uuid:{d['uuid']}" if d.get("uuid") else path,
                        label=d.get("label") or model or PurePosixPath(path).name,
                        mount_point=Path(),
                        fs_type=fs,
                        total=_int(d.get("size")),
                        used=0,
                        free=0,
                        is_removable=True,
                        is_read_only=_flag(d.get("ro")),
                        device_name=path,
                        is_mounted=False,
                    )
                )
            walk(kids, {"removable": removable, "model": model, "tran": d.get("tran") or ""})

    try:
        data = json.loads(text)
    except ValueError:
        return []
    if isinstance(data, dict):
        walk(data.get("blockdevices") or [], {"removable": False, "model": "", "tran": ""})
    return out


def parse_mount_output(text: str) -> Path | None:
    """Mount point from udisksctl's "Mounted /dev/x at /media/u/LABEL" (older versions add '.')."""
    head, sep, tail = text.strip().partition(" at ")
    if not sep or not head.startswith("Mounted "):
        return None
    tail = tail.rstrip()
    if tail.endswith(".") and not Path(tail).exists():
        tail = tail[:-1]
    return Path(tail) if tail else None


def build_volumes(
    partitions: Iterable[Partition],
    devices: dict[str, dict],
    usage: Callable[[str, bool], Usage | None],
) -> list[Volume]:
    """Turn mount table rows + lsblk facts into Volumes. Pure: no OS calls except `usage`."""
    seen: set[str] = set()
    ids: set[str] = set()
    vols: list[Volume] = []
    for p in sorted(partitions, key=lambda p: len(p.mountpoint)):
        fs = p.fstype or ""
        if fs in SKIP_FS or not p.mountpoint:
            continue
        is_dev = PurePosixPath(p.device).parts[1:2] == ("dev",)
        key = p.device if is_dev else f"{fs}:{p.mountpoint}"
        if key in seen:  # bind mount or second mount of the same filesystem
            continue
        is_network = fs in NETWORK_FS
        u = usage(p.mountpoint, is_network)
        if u is None:
            if not is_network:
                continue  # vanished or unreadable local mount
            u = (0, 0, 0)  # unresponsive share: show it, without sizes
        elif u[0] == 0 and not is_network:
            continue  # empty pseudo filesystem we do not know about
        seen.add(key)
        mount = Path(p.mountpoint)
        info = devices.get(p.device) or devices.get(p.mountpoint) or {}
        vol_id = key
        if info.get("uuid") and f"uuid:{info['uuid']}" not in ids:  # clones share a UUID
            vol_id = f"uuid:{info['uuid']}"
        ids.add(vol_id)
        parts = mount.parts
        is_system = len(parts) == 1 or parts[1] in SYSTEM_TOP
        under_media = parts[1:2] == ("media",) or parts[1:3] == ("run", "media")
        is_removable = bool(info.get("removable")) or info.get("tran") == "usb"
        if not info and under_media:
            is_removable = True
        label = info.get("label") or (info.get("model") if is_removable else "") or mount.name
        vols.append(
            Volume(
                id=vol_id,
                label=label or p.device or str(mount),
                mount_point=mount,
                fs_type=fs,
                total=u[0],
                used=u[1],
                free=u[2],
                is_removable=is_removable and not is_system,
                is_network=is_network,
                is_read_only="ro" in p.opts.split(",") or bool(info.get("ro")),
                device_name=p.device,
                is_system=is_system,
            )
        )
    vols.sort(key=lambda v: (not v.is_system, v.is_removable, str(v.mount_point)))
    return vols


def _run_lsblk() -> str:
    try:
        r = subprocess.run(
            ["lsblk", "-J", "-b", "-o", LSBLK_COLUMNS],
            capture_output=True,
            text=True,
            timeout=LSBLK_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout if r.returncode == 0 else ""


def _usage(mount: str, slow: bool) -> Usage | None:
    import psutil

    def get() -> Usage | None:
        try:
            u = psutil.disk_usage(mount)
        except OSError:
            return None
        return (u.total, u.used, u.free)

    if not slow:
        return get()
    box: list[Usage | None] = []
    t = threading.Thread(target=lambda: box.append(get()), daemon=True, name="usage")
    t.start()
    t.join(USAGE_TIMEOUT)  # a hung share leaves only a daemon thread behind
    return box[0] if box else None


def _show_items(path: Path) -> bool:
    """Ask the session file manager to select path. False if none answers in time."""
    uri = path.absolute().as_uri()  # percent-encoded: no quotes or commas reach gdbus
    cmd = ["gdbus", "call", "--session", "--dest", FM1, "--object-path", FM1_PATH]
    cmd += ["--method", f"{FM1}.ShowItems", f"['{uri}']", "''"]
    try:
        done = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=REVEAL_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0


@functools.cache
def _libc_statx():
    """glibc statx(), or None (musl, old glibc). Python 3.11 has no birth time on Linux."""
    import ctypes

    try:
        fn = ctypes.CDLL(None, use_errno=True).statx
    except (OSError, AttributeError):
        return None
    fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_uint, ctypes.c_void_p]
    fn.restype = ctypes.c_int
    return fn


def _statx_btime(path: Path) -> int | None:
    """Birth time via statx; None where the filesystem or kernel doesn't record it."""
    fn = _libc_statx()
    if fn is None:
        return None
    import ctypes

    buf = ctypes.create_string_buffer(STATX_SIZE)
    if fn(AT_FDCWD, os.fsencode(path), AT_SYMLINK_NOFOLLOW, STATX_BTIME, buf) != 0:
        err = ctypes.get_errno()
        if err == errno.ENOSYS:
            return None
        raise OSError(err, os.strerror(err), str(path))
    (mask,) = struct.unpack_from("=I", buf, 0)
    if not mask & STATX_BTIME:
        return None
    (sec,) = struct.unpack_from("=q", buf, STATX_BTIME_OFFSET)
    return sec or None


class LinuxPlatform(LocalPlatform):
    def list_volumes(self) -> list[Volume]:
        import psutil  # lazy: not needed for scans

        try:
            parts = psutil.disk_partitions(all=True)
        except OSError:
            return []
        return build_volumes(parts, parse_lsblk(_run_lsblk()), _usage)

    def list_unmounted(self) -> list[Volume]:
        return unmounted_from_lsblk(_run_lsblk())

    def list_all_volumes(self) -> list[Volume]:
        import psutil

        text = _run_lsblk()  # once per poll: the watcher calls this every few seconds
        try:
            mounted = build_volumes(psutil.disk_partitions(all=True), parse_lsblk(text), _usage)
        except OSError:
            mounted = []
        taken = {v.device_name for v in mounted} | {v.id for v in mounted}
        loose = [v for v in unmounted_from_lsblk(text) if not {v.device_name, v.id} & taken]
        return mounted + loose

    def mount(self, device: str) -> Path:
        cmd = ["udisksctl", "mount", "--no-user-interaction", "-b", device]
        try:
            r = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=MOUNT_TIMEOUT,
                check=False,
            )
        except FileNotFoundError as e:
            raise OSError(errno.ENOTSUP, "udisksctl is not installed.") from e
        except subprocess.TimeoutExpired as e:
            raise OSError(errno.ETIMEDOUT, "Mounting took too long.") from e
        mount = parse_mount_output(r.stdout) if r.returncode == 0 else None
        if mount is None:
            msg = (r.stderr or r.stdout).strip().splitlines()
            raise OSError(errno.EIO, msg[-1] if msg else "The drive could not be mounted.")
        return mount

    def owner_and_perms(self, path: Path) -> Permissions:
        import grp
        import pwd

        s = os.lstat(path)
        try:
            owner = pwd.getpwuid(s.st_uid).pw_name
        except KeyError:
            owner = str(s.st_uid)
        try:
            group = grp.getgrgid(s.st_gid).gr_name
        except KeyError:
            group = str(s.st_gid)
        writable = os.access(path, os.W_OK, follow_symlinks=False)  # False on read-only mounts
        return Permissions(owner, group, st.filemode(s.st_mode), not writable)

    def is_hidden(self, path: Path) -> bool:
        return path.name.startswith(".")

    def created(self, path: Path) -> int | None:
        return _statx_btime(path)

    def reveal(self, path: Path) -> None:
        """Folders open in the file manager; files open their folder with the file selected
        (FileManager1.ShowItems: Thunar, Nautilus, Dolphin, ...), else just the folder."""
        s = os.lstat(path)  # raises if gone
        if st.S_ISDIR(s.st_mode):
            target = path
        elif _show_items(path):
            return
        else:
            target = path.parent
        subprocess.Popen(
            ["xdg-open", str(target)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
