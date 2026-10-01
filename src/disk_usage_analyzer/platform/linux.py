"""Linux platform: volumes from psutil (mount table) + lsblk (device facts), xdg-open reveal."""

from __future__ import annotations

import json
import os
import stat as st
import subprocess
import threading
from collections.abc import Callable, Iterable
from pathlib import Path, PurePosixPath
from typing import Protocol

from .base import Permissions, Volume
from .local import LocalPlatform

LSBLK_TIMEOUT = 3.0  # seconds
USAGE_TIMEOUT = 2.0  # network mounts can hang statvfs
LSBLK_COLUMNS = "NAME,PATH,MOUNTPOINT,LABEL,MODEL,TYPE,TRAN,RM,HOTPLUG,RO"

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


def build_volumes(
    partitions: Iterable[Partition],
    devices: dict[str, dict],
    usage: Callable[[str, bool], Usage | None],
) -> list[Volume]:
    """Turn mount table rows + lsblk facts into Volumes. Pure: no OS calls except `usage`."""
    seen: set[str] = set()
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
        parts = mount.parts
        is_system = len(parts) == 1 or parts[1] in SYSTEM_TOP
        under_media = parts[1:2] == ("media",) or parts[1:3] == ("run", "media")
        is_removable = bool(info.get("removable")) or info.get("tran") == "usb"
        if not info and under_media:
            is_removable = True
        label = info.get("label") or (info.get("model") if is_removable else "") or mount.name
        vols.append(
            Volume(
                id=key,
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
            ["lsblk", "-J", "-o", LSBLK_COLUMNS],
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


class LinuxPlatform(LocalPlatform):
    def list_volumes(self) -> list[Volume]:
        import psutil  # lazy: not needed for scans

        try:
            parts = psutil.disk_partitions(all=True)
        except OSError:
            return []
        return build_volumes(parts, parse_lsblk(_run_lsblk()), _usage)

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

    def reveal(self, path: Path) -> None:
        s = os.lstat(path)  # raises if gone
        target = path if st.S_ISDIR(s.st_mode) else path.parent
        subprocess.Popen(
            ["xdg-open", str(target)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
