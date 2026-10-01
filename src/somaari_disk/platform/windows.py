"""Windows platform: kernel32/advapi32 via ctypes (stdlib only). See docs/PLATFORM.md.

Pure helpers (long_path, round_up, make_volume, ...) run on any OS so they are tested on Linux
too; everything that calls the Win32 API is only reached on Windows.
"""

from __future__ import annotations

import ctypes
import functools
import logging
import ntpath
import os
import stat as st
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple, TypeVar

from .base import EntryInfo, Listing, Permissions, Volume
from .local import LocalPlatform, _info

log = logging.getLogger(__name__)

QUERY_TIMEOUT = 2.0  # seconds; network shares and empty optical drives can hang
LONG_PATH_AT = 248  # MAX_PATH (260) minus room for an 8.3 name; longer paths need \\?\
_LONG_PREFIX = "\\\\?\\"

# GetDriveType
DRIVE_UNKNOWN, DRIVE_NO_ROOT_DIR = 0, 1
DRIVE_REMOVABLE, DRIVE_FIXED, DRIVE_REMOTE, DRIVE_CDROM, DRIVE_RAMDISK = 2, 3, 4, 5, 6
FILE_READ_ONLY_VOLUME = 0x00080000
# STORAGE_BUS_TYPE values for external media: 1394, USB, SD, MMC
_EXTERNAL_BUSES = frozenset({0x4, 0x7, 0xC, 0xD})

# File attributes
A_READONLY, A_HIDDEN, A_SYSTEM, A_ARCHIVE = 0x1, 0x2, 0x4, 0x20
A_SPARSE, A_COMPRESSED, A_OFFLINE, A_ENCRYPTED = 0x200, 0x800, 0x1000, 0x4000
A_RECALL_ON_OPEN, A_RECALL_ON_DATA_ACCESS = 0x40000, 0x400000  # cloud placeholders (OneDrive)
# Files whose allocation isn't just "size rounded up to a cluster": ask the filesystem.
_ASK_OS = A_SPARSE | A_COMPRESSED | A_OFFLINE | A_RECALL_ON_OPEN | A_RECALL_ON_DATA_ACCESS
_ATTR_NAMES = (
    (A_HIDDEN, "Hidden"),
    (A_SYSTEM, "System"),
    (A_ARCHIVE, "Archive"),
    (A_COMPRESSED, "Compressed"),
    (A_ENCRYPTED, "Encrypted"),
    (A_OFFLINE | A_RECALL_ON_OPEN | A_RECALL_ON_DATA_ACCESS, "Online-only"),
)
_DEFAULT_LABELS = {
    DRIVE_FIXED: "Local Disk",
    DRIVE_REMOVABLE: "Removable Disk",
    DRIVE_REMOTE: "Network Drive",
    DRIVE_CDROM: "CD Drive",
    DRIVE_RAMDISK: "RAM Disk",
}

T = TypeVar("T")


# ---- pure helpers --------------------------------------------------------------------


def long_path(path: Path | str) -> str:
    """Path string the Win32 API accepts beyond MAX_PATH (\\\\?\\ prefix), else unchanged."""
    s = str(path)
    if len(s) < LONG_PATH_AT or s.startswith(_LONG_PREFIX) or not ntpath.isabs(s):
        return s
    if s.startswith("\\\\"):  # UNC: \\server\share\... -> \\?\UNC\server\share\...
        return _LONG_PREFIX + "UNC" + s[1:]
    return _LONG_PREFIX + s


def round_up(size: int, cluster: int) -> int:
    """Bytes allocated for size bytes on a volume with this cluster size (0 = unknown)."""
    return -(-size // cluster) * cluster if cluster > 0 else size


def attr_text(attrs: int) -> str:
    """Display string for file attributes (read-only is reported separately)."""
    return ", ".join(name for bit, name in _ATTR_NAMES if attrs & bit) or "Normal"


def parse_storage_descriptor(data: bytes) -> tuple[int | None, str]:
    """(bus type, "vendor product") from a STORAGE_DEVICE_DESCRIPTOR buffer."""
    if len(data) < 32:
        return None, ""

    def text(at: int) -> str:
        off = int.from_bytes(data[at : at + 4], "little")
        if not 0 < off < len(data):
            return ""
        end = data.find(b"\0", off)
        return data[off : end if end >= 0 else None].decode("ascii", "replace").strip()

    bus = int.from_bytes(data[28:32], "little")
    return bus, " ".join(t for t in (text(12), text(16)) if t)


class RawVolume(NamedTuple):
    """What the Win32 queries return for one drive root."""

    label: str
    fs_type: str
    serial: int
    flags: int
    total: int
    total_free: int
    avail: int  # free bytes for this user (quotas)
    guid_path: str = ""  # \\?\Volume{...}\ for local volumes
    bus: int | None = None
    model: str = ""


def volume_id(raw: RawVolume, root: str) -> str:
    """Stable id across replug: volume GUID, else serial number, else fs type + root."""
    g = raw.guid_path
    if "{" in g and "}" in g:
        return "guid:" + g[g.index("{") : g.index("}") + 1].lower()
    if raw.serial:
        return f"serial:{raw.serial:08X}"
    return f"{raw.fs_type.lower() or 'drive'}:{root}"


def make_volume(root: str, drive_type: int, raw: RawVolume | None, system_drive: str) -> Volume:
    """Volume for a drive root ("E:\\"). raw None = network share that didn't answer in time."""
    network = drive_type == DRIVE_REMOTE
    is_system = root[:2].upper() == system_drive[:2].upper()
    if raw is None:
        label = _DEFAULT_LABELS.get(drive_type, "Drive")
        return Volume(f"drive:{root}", label, Path(root), "", 0, 0, 0, is_network=network)
    removable = drive_type in (DRIVE_REMOVABLE, DRIVE_CDROM) or (
        raw.bus in _EXTERNAL_BUSES and not is_system
    )
    return Volume(
        id=volume_id(raw, root),
        label=raw.label or _DEFAULT_LABELS.get(drive_type, "Drive"),
        mount_point=Path(root),
        fs_type=raw.fs_type,
        total=raw.total,
        used=max(raw.total - raw.total_free, 0),
        free=raw.avail,
        is_removable=removable,
        is_network=network,
        is_read_only=bool(raw.flags & FILE_READ_ONLY_VOLUME),
        device_name=raw.model,
        is_system=is_system,
    )


def _timed(fn: Callable[[], T], timeout: float) -> T | None:
    """fn() in a daemon thread; None if it fails or doesn't finish in time."""
    box: list[T] = []

    def run() -> None:
        try:
            box.append(fn())
        except (OSError, ValueError):
            pass

    t = threading.Thread(target=run, daemon=True, name="volume-query")
    t.start()
    t.join(timeout)  # a hung share leaves only a daemon thread behind
    return box[0] if box else None


# ---- Win32 ---------------------------------------------------------------------------


@functools.cache
def _k32():
    from ctypes import wintypes as w

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    u64p = ctypes.POINTER(ctypes.c_ulonglong)
    dwp = ctypes.POINTER(w.DWORD)
    sigs = {
        "GetLogicalDrives": ([], w.DWORD),
        "GetDriveTypeW": ([w.LPCWSTR], w.UINT),
        "GetVolumeInformationW": (
            [w.LPCWSTR, w.LPWSTR, w.DWORD, dwp, dwp, dwp, w.LPWSTR, w.DWORD],
            w.BOOL,
        ),
        "GetDiskFreeSpaceExW": ([w.LPCWSTR, u64p, u64p, u64p], w.BOOL),
        "GetDiskFreeSpaceW": ([w.LPCWSTR, dwp, dwp, dwp, dwp], w.BOOL),
        "GetVolumeNameForVolumeMountPointW": ([w.LPCWSTR, w.LPWSTR, w.DWORD], w.BOOL),
        "GetCompressedFileSizeW": ([w.LPCWSTR, dwp], w.DWORD),
        "SetThreadErrorMode": ([w.DWORD, dwp], w.BOOL),
        "CreateFileW": (
            [w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE],
            w.HANDLE,
        ),
        "DeviceIoControl": (
            [w.HANDLE, w.DWORD, w.LPVOID, w.DWORD, w.LPVOID, w.DWORD, dwp, w.LPVOID],
            w.BOOL,
        ),
        "CloseHandle": ([w.HANDLE], w.BOOL),
        "LocalFree": ([w.HLOCAL], w.HLOCAL),
    }
    for name, (args, res) in sigs.items():
        f = getattr(k, name)
        f.argtypes, f.restype = args, res
    return k


@functools.cache
def _adv32():
    from ctypes import wintypes as w

    a = ctypes.WinDLL("advapi32", use_last_error=True)
    vp = ctypes.POINTER(w.LPVOID)
    dwp = ctypes.POINTER(w.DWORD)
    a.GetNamedSecurityInfoW.argtypes = [w.LPCWSTR, ctypes.c_int, w.DWORD, vp, vp, vp, vp, vp]
    a.GetNamedSecurityInfoW.restype = w.DWORD
    a.LookupAccountSidW.argtypes = [w.LPCWSTR, w.LPVOID, w.LPWSTR, dwp, w.LPWSTR, dwp, dwp]
    a.LookupAccountSidW.restype = w.BOOL
    a.ConvertSidToStringSidW.argtypes = [w.LPVOID, ctypes.POINTER(w.LPWSTR)]
    a.ConvertSidToStringSidW.restype = w.BOOL
    return a


def _dword():
    from ctypes import wintypes

    return wintypes.DWORD()


def _storage_descriptor(letter: str) -> tuple[int | None, str]:
    """Bus type and model of the disk behind a drive letter. Needs no admin rights."""
    k = _k32()
    share_rw, open_existing = 0x3, 3
    h = k.CreateFileW(f"\\\\.\\{letter}:", 0, share_rw, None, open_existing, 0, None)
    if not h or h == ctypes.c_void_p(-1).value:
        return None, ""
    try:
        query = (ctypes.c_ubyte * 12)()  # StorageDeviceProperty, PropertyStandardQuery
        buf = (ctypes.c_ubyte * 1024)()
        got = _dword()
        ioctl_storage_query_property = 0x2D1400
        ok = k.DeviceIoControl(
            h, ioctl_storage_query_property, query, 12, buf, 1024, ctypes.byref(got), None
        )
        return parse_storage_descriptor(bytes(buf[: got.value])) if ok else (None, "")
    finally:
        k.CloseHandle(h)


def _query_volume(root: str, drive_type: int) -> RawVolume | None:
    """Label, filesystem, sizes and disk info for a drive root. None if no media / no answer."""
    k = _k32()
    sem_failcriticalerrors = 0x1
    k.SetThreadErrorMode(sem_failcriticalerrors, None)  # no "insert a disk" dialogs
    name, fs = ctypes.create_unicode_buffer(261), ctypes.create_unicode_buffer(261)
    serial, max_comp, flags = _dword(), _dword(), _dword()
    by = ctypes.byref
    if not k.GetVolumeInformationW(root, name, 261, by(serial), by(max_comp), by(flags), fs, 261):
        return None  # empty card reader or optical drive, locked BitLocker volume, ...
    avail, total, total_free = ctypes.c_ulonglong(), ctypes.c_ulonglong(), ctypes.c_ulonglong()
    if not k.GetDiskFreeSpaceExW(root, by(avail), by(total), by(total_free)):
        avail.value = total.value = total_free.value = 0
    guid = ctypes.create_unicode_buffer(64)
    guid_path = guid.value if k.GetVolumeNameForVolumeMountPointW(root, guid, 64) else ""
    bus, model = (None, "")
    if drive_type in (DRIVE_FIXED, DRIVE_REMOVABLE):
        bus, model = _storage_descriptor(root[0])
    return RawVolume(
        name.value,
        fs.value,
        serial.value,
        flags.value,
        total.value,
        total_free.value,
        avail.value,
        guid_path,
        bus,
        model,
    )


def _cluster_size(root: str) -> int:
    spc, bps, free_c, total_c = _dword(), _dword(), _dword(), _dword()
    by = ctypes.byref
    if not _k32().GetDiskFreeSpaceW(root, by(spc), by(bps), by(free_c), by(total_c)):
        return 0
    return spc.value * bps.value


def _compressed_size(path: str) -> int | None:
    """Bytes actually allocated (compressed, sparse, cloud placeholder). None on failure."""
    k = _k32()
    high = _dword()
    ctypes.set_last_error(0)
    low = k.GetCompressedFileSizeW(path, ctypes.byref(high))
    if low == 0xFFFFFFFF and ctypes.get_last_error():
        return None
    return (high.value << 32) | low


def _owner(path: str) -> str:
    """Owner account name, else its SID string, else ""."""
    from ctypes import wintypes as w

    adv, k = _adv32(), _k32()
    se_file_object, owner_security_information = 1, 1
    sid, sd = w.LPVOID(), w.LPVOID()
    by = ctypes.byref
    rc = adv.GetNamedSecurityInfoW(
        path, se_file_object, owner_security_information, by(sid), None, None, None, by(sd)
    )
    if rc:
        return ""
    try:
        name, dom = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(256)
        n, d, use = w.DWORD(256), w.DWORD(256), w.DWORD()
        if adv.LookupAccountSidW(None, sid, name, by(n), dom, by(d), by(use)):
            return name.value
        text = w.LPWSTR()
        if adv.ConvertSidToStringSidW(sid, by(text)):
            try:
                return text.value or ""
            finally:
                k.LocalFree(ctypes.cast(text, w.LPVOID))
        return ""
    finally:
        k.LocalFree(sd)


WEBVIEW2_CLIENT = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"  # EdgeUpdate id of the Runtime


def webview2_version() -> str:
    """Installed WebView2 Runtime version, "" if missing. Checks per-machine (64- and 32-bit
    views) and per-user installs, as Microsoft documents for runtime detection."""
    import winreg

    key = rf"Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT}"
    places = (
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\{key}"),
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\{key}"),
        (winreg.HKEY_CURRENT_USER, rf"Software\{key}"),
    )
    for hive, sub in places:
        try:
            with winreg.OpenKey(hive, sub) as k:
                version, _ = winreg.QueryValueEx(k, "pv")
        except OSError:
            continue
        if version and version != "0.0.0.0":
            return str(version)
    return ""


def message_box(title: str, text: str) -> None:
    """Native error dialog, for failures before the window exists."""
    mb_iconerror = 0x10
    ctypes.WinDLL("user32").MessageBoxW(None, text, title, mb_iconerror)


# ---- platform ------------------------------------------------------------------------


class WindowsPlatform(LocalPlatform):
    def __init__(self) -> None:
        self._clusters: dict[str, int] = {}  # drive anchor -> cluster size

    def _cluster(self, path: Path) -> int:
        anchor = path.anchor.upper()
        if anchor not in self._clusters:
            try:
                self._clusters[anchor] = _cluster_size(anchor)
            except OSError:
                self._clusters[anchor] = 0
        return self._clusters[anchor]

    def _entry(self, name: str, full: str, s: os.stat_result, cluster: int) -> EntryInfo:
        if not st.S_ISREG(s.st_mode):
            return _info(name, s, 0)
        if not (s.st_ino and s.st_nlink):  # scandir's cached stat: no file index, links, serial
            s = os.lstat(full)
        attrs = getattr(s, "st_file_attributes", 0)
        allocated = _compressed_size(full) if attrs & _ASK_OS else None
        size = s.st_size if allocated is None else allocated
        return _info(name, s, round_up(size, cluster))

    def stat(self, path: Path) -> EntryInfo:
        full = long_path(path)
        return self._entry(path.name or str(path), full, os.lstat(full), self._cluster(path))

    def scandir(self, path: Path) -> Listing:
        listing = Listing()
        cluster = self._cluster(path)
        with os.scandir(long_path(path)) as it:
            for entry in it:
                try:
                    s = entry.stat(follow_symlinks=False)
                    listing.entries.append(self._entry(entry.name, entry.path, s, cluster))
                except OSError as e:
                    listing.errors.append((entry.name, e))
        return listing

    def created(self, path: Path) -> int | None:
        s = os.lstat(long_path(path))
        birth = getattr(s, "st_birthtime", None) or s.st_ctime  # st_ctime = creation before 3.12
        return int(birth) if birth else None

    def list_volumes(self) -> list[Volume]:
        system_drive = os.environ.get("SystemDrive", "C:")
        volumes: list[Volume] = []
        try:
            k = _k32()
            mask = k.GetLogicalDrives()
        except (OSError, AttributeError) as e:
            log.warning("list volumes failed: %s", e)
            return volumes
        for i in range(26):
            if not mask & (1 << i):
                continue
            root = f"{chr(ord('A') + i)}:\\"
            try:
                drive_type = k.GetDriveTypeW(root)
                if drive_type in (DRIVE_UNKNOWN, DRIVE_NO_ROOT_DIR):
                    continue
                if drive_type == DRIVE_FIXED:
                    raw = _query_volume(root, drive_type)
                else:  # network, optical and card readers can block for seconds
                    raw = _timed(functools.partial(_query_volume, root, drive_type), QUERY_TIMEOUT)
                if raw is None and drive_type != DRIVE_REMOTE:
                    continue  # no media
                volumes.append(make_volume(root, drive_type, raw, system_drive))
            except (OSError, ValueError) as e:
                log.warning("volume query failed: %s", e)
        return volumes

    def owner_and_perms(self, path: Path) -> Permissions:
        full = long_path(path)
        s = os.lstat(full)  # raises if gone
        attrs = getattr(s, "st_file_attributes", 0)
        read_only = bool(attrs & A_READONLY)
        if not read_only and path.anchor:
            vol = _timed(lambda: _query_volume(path.anchor, DRIVE_UNKNOWN), QUERY_TIMEOUT)
            read_only = bool(vol and vol.flags & FILE_READ_ONLY_VOLUME)
        return Permissions(_owner(full), "", attr_text(attrs), read_only)

    def is_hidden(self, path: Path) -> bool:
        try:
            attrs = getattr(os.lstat(long_path(path)), "st_file_attributes", 0)
        except OSError:
            attrs = 0
        return bool(attrs & A_HIDDEN) or path.name.startswith(".")

    def reveal(self, path: Path) -> None:
        """Folders open in Explorer; files open their folder with the file selected."""
        s = os.lstat(long_path(path))  # raises if gone
        target = str(path)
        # One string, not a list: explorer parses /select, itself and mis-reads list2cmdline
        # quoting. Windows paths can't contain '"', so this quoting is safe.
        cmd = f'explorer "{target}"' if st.S_ISDIR(s.st_mode) else f'explorer /select,"{target}"'
        subprocess.Popen(  # explorer exits 1 even on success: don't wait for it
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
