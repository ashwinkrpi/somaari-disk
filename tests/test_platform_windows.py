"""Windows platform: pure helpers on every OS, real Win32 calls (tmp_path only) on Windows."""

import errno
import os
import stat
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from disk_usage_analyzer.api.bridge import _os_error
from disk_usage_analyzer.core.scanner import Scanner, device_gone
from disk_usage_analyzer.platform import windows as win
from disk_usage_analyzer.platform.base import Kind
from disk_usage_analyzer.platform.windows import (
    DRIVE_CDROM,
    DRIVE_FIXED,
    DRIVE_REMOTE,
    DRIVE_REMOVABLE,
    FILE_READ_ONLY_VOLUME,
    RawVolume,
    WindowsPlatform,
    attr_text,
    long_path,
    make_volume,
    parse_storage_descriptor,
    round_up,
    volume_id,
)

WINDOWS_ONLY = pytest.mark.skipif(sys.platform != "win32", reason="real Win32 API")
GB = 10**9
GUID = "\\\\?\\Volume{0A1B2C3D-0000-0000-0000-000000000001}\\"


# ---- pure helpers --------------------------------------------------------------------


def test_long_path():
    assert long_path("C:\\short\\path") == "C:\\short\\path"
    deep = "C:\\" + "\\".join(["d" * 50] * 6)
    assert long_path(deep) == "\\\\?\\" + deep
    assert long_path("\\\\?\\" + deep) == "\\\\?\\" + deep  # already prefixed
    unc = "\\\\server\\share\\" + "\\".join(["d" * 50] * 6)
    assert long_path(unc) == "\\\\?\\UNC\\server\\share\\" + "\\".join(["d" * 50] * 6)
    rel = "\\".join(["d" * 50] * 6)
    assert long_path(rel) == rel  # relative paths can't take the prefix


@pytest.mark.parametrize(
    ("size", "cluster", "expected"),
    [(0, 4096, 0), (1, 4096, 4096), (4096, 4096, 4096), (4097, 4096, 8192), (123, 0, 123)],
)
def test_round_up(size, cluster, expected):
    assert round_up(size, cluster) == expected


def test_attr_text():
    assert attr_text(0) == "Normal"
    assert attr_text(win.A_READONLY) == "Normal"  # read-only is its own flag
    assert attr_text(win.A_HIDDEN | win.A_SYSTEM) == "Hidden, System"
    assert attr_text(win.A_RECALL_ON_DATA_ACCESS | win.A_ARCHIVE) == "Archive, Online-only"


def descriptor(bus: int, vendor: bytes = b"", product: bytes = b"") -> bytes:
    head = bytearray(36)
    tail = bytearray()
    for at, text in ((12, vendor), (16, product)):
        if text:
            head[at : at + 4] = (36 + len(tail)).to_bytes(4, "little")
            tail += text + b"\0"
    head[28:32] = bus.to_bytes(4, "little")
    return bytes(head + tail)


def test_parse_storage_descriptor():
    assert parse_storage_descriptor(descriptor(7, b"Sample  ", b"Flash")) == (7, "Sample Flash")
    assert parse_storage_descriptor(descriptor(0x11)) == (0x11, "")
    assert parse_storage_descriptor(b"\0" * 10) == (None, "")
    bad = bytearray(descriptor(7))
    bad[16:20] = (5000).to_bytes(4, "little")  # offset past the buffer
    assert parse_storage_descriptor(bytes(bad)) == (7, "")


def raw(**kw) -> RawVolume:
    base = dict(
        label="DATA", fs_type="NTFS", serial=0x1234ABCD, flags=0,
        total=100 * GB, total_free=40 * GB, avail=30 * GB, guid_path=GUID, bus=0x11, model="",
    )  # fmt: skip
    return RawVolume(**{**base, **kw})


def test_volume_id_prefers_guid_then_serial():
    assert volume_id(raw(), "E:\\") == "guid:{0a1b2c3d-0000-0000-0000-000000000001}"
    assert volume_id(raw(guid_path=""), "E:\\") == "serial:1234ABCD"
    assert volume_id(raw(guid_path="", serial=0, fs_type="exFAT"), "E:\\") == "exfat:E:\\"


def test_make_volume_system_disk():
    v = make_volume("C:\\", DRIVE_FIXED, raw(model="Sample NVMe"), "C:")
    assert (v.mount_point, v.fs_type, v.label, v.device_name) == (
        Path("C:\\"),
        "NTFS",
        "DATA",
        "Sample NVMe",
    )
    assert (v.total, v.used, v.free) == (100 * GB, 60 * GB, 30 * GB)
    assert v.is_system and not (v.is_removable or v.is_network or v.is_read_only)


def test_make_volume_external_and_removable():
    usb_hdd = make_volume("E:\\", DRIVE_FIXED, raw(bus=0x7, fs_type="exFAT"), "C:")
    assert usb_hdd.is_removable and not usb_hdd.is_system
    sd = make_volume("F:\\", DRIVE_REMOVABLE, raw(bus=0xC, fs_type="FAT32", label=""), "C:")
    assert sd.is_removable and sd.label == "Removable Disk"
    cd = make_volume("G:\\", DRIVE_CDROM, raw(flags=FILE_READ_ONLY_VOLUME, bus=None), "C:")
    assert cd.is_removable and cd.is_read_only
    internal = make_volume("D:\\", DRIVE_FIXED, raw(bus=0x11), "C:")
    assert not internal.is_removable


def test_make_volume_network():
    v = make_volume("Z:\\", DRIVE_REMOTE, raw(guid_path="", bus=None), "C:")
    assert v.is_network and v.id == "serial:1234ABCD"
    hung = make_volume("Z:\\", DRIVE_REMOTE, None, "C:")  # didn't answer in time
    assert hung.is_network and (hung.total, hung.used, hung.free) == (0, 0, 0)
    assert hung.label == "Network Drive" and hung.id == "drive:Z:\\"


def test_timed():
    assert win._timed(lambda: 5, 1.0) == 5
    assert win._timed(lambda: time.sleep(1) or 1, 0.05) is None

    def boom():
        raise OSError(errno.EIO, "x")

    assert win._timed(boom, 1.0) is None


# ---- WindowsPlatform logic with the Win32 calls stubbed (any OS) ----------------------


def fake_stat(size=5000, attrs=0, nlink=1, ino=7):
    return SimpleNamespace(
        st_mode=stat.S_IFREG | 0o644, st_size=size, st_mtime=1.5e9, st_dev=9,
        st_ino=ino, st_nlink=nlink, st_file_attributes=attrs,
    )  # fmt: skip


def test_entry_rounds_to_cluster():
    e = WindowsPlatform()._entry("f", "f", fake_stat(5000), 4096)
    assert (e.kind, e.size, e.size_on_disk, e.file_id) == (Kind.FILE, 5000, 8192, None)


def test_entry_asks_os_for_compressed_and_cloud_files(monkeypatch):
    asked = []
    monkeypatch.setattr(win, "_compressed_size", lambda p: asked.append(p) or 1000)
    p = WindowsPlatform()
    assert p._entry("f", "f", fake_stat(10**6, win.A_COMPRESSED), 4096).size_on_disk == 4096
    placeholder = fake_stat(10**6, win.A_RECALL_ON_DATA_ACCESS)
    monkeypatch.setattr(win, "_compressed_size", lambda p: 0)
    assert p._entry("f", "f", placeholder, 4096).size_on_disk == 0
    monkeypatch.setattr(win, "_compressed_size", lambda p: None)  # API failed: fall back
    assert p._entry("f", "f", fake_stat(5000, win.A_SPARSE), 4096).size_on_disk == 8192
    assert asked == ["f"]


def test_entry_hard_link_and_dir():
    p = WindowsPlatform()
    assert p._entry("f", "f", fake_stat(nlink=2, ino=77), 0).file_id == 77
    d = SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_size=0, st_mtime=0, st_dev=9, st_ino=1,
                        st_nlink=1, st_file_attributes=0)  # fmt: skip
    assert p._entry("d", "d", d, 4096).kind is Kind.DIR


def test_entry_refetches_scandir_stat(monkeypatch, tmp_path):
    """scandir on Windows has no file index or link count: the entry re-stats the file."""
    f = tmp_path / "f"
    f.write_bytes(b"x" * 10)
    e = WindowsPlatform()._entry("f", str(f), fake_stat(10, ino=0, nlink=0), 0)
    assert e.dev == (os.lstat(f).st_dev or None)


def test_scandir_and_hard_links(monkeypatch, tmp_path):
    monkeypatch.setattr(win, "_cluster_size", lambda root: 4096)
    (tmp_path / "a").write_bytes(b"h" * 5000)
    os.link(tmp_path / "a", tmp_path / "b")
    (tmp_path / "d").mkdir()
    p = WindowsPlatform()
    entries = {e.name: e for e in p.scandir(tmp_path).entries}
    assert entries["a"].size_on_disk == 8192
    assert entries["a"].file_id is not None and entries["a"].file_id == entries["b"].file_id
    assert entries["d"].kind is Kind.DIR
    r = Scanner(p).scan(tmp_path)
    assert r.root.size == 5000 and r.hardlinks_deduped == 1


def test_reveal_command(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(win.subprocess, "Popen", lambda cmd, **kw: calls.append(cmd))
    (tmp_path / "f").write_bytes(b"x")
    p = WindowsPlatform()
    p.reveal(tmp_path / "f")
    p.reveal(tmp_path)
    assert calls == [f'explorer /select,"{tmp_path / "f"}"', f'explorer "{tmp_path}"']
    with pytest.raises(FileNotFoundError):
        p.reveal(tmp_path / "gone")


def test_list_volumes_never_raises_without_win32(monkeypatch):
    def no_api():
        raise OSError("no kernel32")

    monkeypatch.setattr(win, "_k32", no_api)
    assert WindowsPlatform().list_volumes() == []


# ---- device gone (Windows error codes) -------------------------------------------------


def win_error(code: int) -> OSError:
    e = OSError(errno.EINVAL, "device error")
    e.winerror = code  # what Windows sets; plain attribute elsewhere
    return e


@pytest.mark.parametrize("code", [21, 55, 1167])  # not ready, dev not exist, not connected
def test_windows_device_gone_codes(code):
    assert device_gone(win_error(code))
    assert _os_error(win_error(code))["error"]["code"] == "drive_unavailable"


def test_other_windows_errors_are_not_device_gone():
    assert not device_gone(win_error(5))  # access denied
    assert device_gone(OSError(errno.ENODEV, "gone"))


# ---- real Win32 (Windows CI) ------------------------------------------------------------


@WINDOWS_ONLY
def test_real_list_volumes():
    vols = WindowsPlatform().list_volumes()
    assert vols, "at least the system drive"
    assert len({v.id for v in vols}) == len(vols)
    system = [v for v in vols if v.is_system]
    assert len(system) == 1 and system[0].total > 0 and system[0].fs_type
    assert all(v.used + v.free <= v.total or v.total == 0 for v in vols)


@WINDOWS_ONLY
def test_real_stat_owner_and_flags(tmp_path):
    f = tmp_path / "f"
    f.write_bytes(b"x" * 5000)
    p = WindowsPlatform()
    info = p.stat(f)
    cluster = p._cluster(f)
    assert cluster > 0 and info.size == 5000
    assert info.size_on_disk >= 5000 and info.size_on_disk % cluster == 0
    assert p.created(f) and abs(p.created(f) - time.time()) < 3600
    perms = p.owner_and_perms(f)
    assert perms.owner and perms.group == "" and not perms.is_read_only
    os.chmod(f, stat.S_IREAD)
    try:
        assert p.owner_and_perms(f).is_read_only
    finally:
        os.chmod(f, stat.S_IREAD | stat.S_IWRITE)


@WINDOWS_ONLY
def test_real_hidden_attribute(tmp_path):
    import ctypes

    f = tmp_path / "plain"
    f.write_bytes(b"x")
    p = WindowsPlatform()
    assert not p.is_hidden(f)
    assert ctypes.windll.kernel32.SetFileAttributesW(str(f), win.A_HIDDEN)
    assert p.is_hidden(f)


@WINDOWS_ONLY
def test_real_long_paths(tmp_path):
    deep = tmp_path.joinpath(*["d" * 60] * 5)
    assert len(str(deep)) > 260
    os.makedirs(long_path(deep))
    with open(long_path(deep / "f"), "wb") as fh:
        fh.write(b"x" * 100)
    r = Scanner(WindowsPlatform()).scan(tmp_path)
    assert r.root.size == 100 and not r.warnings


# ---- WebView2 runtime + window backend -----------------------------------------------


def fake_winreg(monkeypatch, values: dict):
    """values: registry subkey -> pv value; other keys raise OSError like winreg does."""

    class Key:
        def __init__(self, sub):
            if sub not in values:
                raise FileNotFoundError(sub)
            self.sub = sub

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    reg = SimpleNamespace(
        HKEY_LOCAL_MACHINE="HKLM",
        HKEY_CURRENT_USER="HKCU",
        OpenKey=lambda hive, sub: Key(sub),
        QueryValueEx=lambda k, name: (values[k.sub], 1),
    )
    monkeypatch.setitem(sys.modules, "winreg", reg)


def test_webview2_version(monkeypatch):
    key = f"Microsoft\\EdgeUpdate\\Clients\\{win.WEBVIEW2_CLIENT}"
    fake_winreg(monkeypatch, {})
    assert win.webview2_version() == ""
    fake_winreg(monkeypatch, {f"SOFTWARE\\WOW6432Node\\{key}": "0.0.0.0"})  # uninstalled
    assert win.webview2_version() == ""
    fake_winreg(monkeypatch, {f"Software\\{key}": "120.0.1.2"})  # per-user install
    assert win.webview2_version() == "120.0.1.2"


def test_gui_backend():
    from disk_usage_analyzer.app import gui_backend

    assert gui_backend("win32") == "edgechromium"
    assert gui_backend("linux") == "gtk"


@WINDOWS_ONLY
def test_real_webview2_installed():
    assert win.webview2_version()  # windows-latest runners ship the Evergreen runtime
