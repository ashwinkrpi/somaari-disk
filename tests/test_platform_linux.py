"""Linux platform: volume parsing with canned mount/lsblk data, plus tmp_path-only file ops."""

import errno
import json
import os
import subprocess
import sys
import types
from collections import namedtuple
from pathlib import Path

import pytest

from somaari_disk.platform import get_platform, linux
from somaari_disk.platform.linux import LinuxPlatform, build_volumes, parse_lsblk

POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0

Part = namedtuple("Part", "device mountpoint fstype opts")
GB = 10**9

LSBLK = {
    "blockdevices": [
        {
            "name": "nvme0n1", "path": "/dev/nvme0n1", "type": "disk", "tran": "nvme",
            "model": "Sample NVMe ", "rm": False, "hotplug": False, "ro": False,
            "children": [
                {"name": "nvme0n1p1", "path": "/dev/nvme0n1p1", "mountpoint": "/boot/firmware",
                 "label": "bootfs", "rm": False, "hotplug": False, "ro": False},
                {"name": "nvme0n1p2", "path": "/dev/nvme0n1p2", "mountpoint": "/",
                 "label": "rootfs", "rm": False, "hotplug": False, "ro": False},
            ],
        },
        {
            "name": "sda", "path": "/dev/sda", "type": "disk", "tran": "usb",
            "model": "Demo Stick", "rm": "1", "hotplug": "1", "ro": "0",
            "children": [
                {"name": "sda1", "path": "/dev/sda1", "mountpoint": "/media/demo/STICK",
                 "label": "STICK", "rm": "1", "hotplug": "1", "ro": "0"},
            ],
        },
        {
            "name": "sdb", "path": "/dev/sdb", "type": "disk", "tran": "usb",
            "model": "Card Reader", "rm": True, "hotplug": True, "ro": False,
            "children": [{"name": "sdb1", "path": "/dev/sdb1", "mountpoint": "/media/demo/sd",
                          "label": None, "rm": True, "hotplug": True, "ro": False}],
        },
    ]
}  # fmt: skip

PARTS = [
    Part("/dev/root", "/", "ext4", "rw,noatime"),
    Part("/dev/nvme0n1p1", "/boot/firmware", "vfat", "rw"),
    Part("/dev/sda1", "/media/demo/STICK", "exfat", "rw,nosuid"),
    Part("/dev/sdb1", "/media/demo/sd", "vfat", "ro,nosuid"),
    Part("/dev/sda1", "/srv/bind-of-stick", "exfat", "rw"),  # second mount: dropped
    Part("proc", "/proc", "proc", "rw"),
    Part("tmpfs", "/run", "tmpfs", "rw"),
    Part("/dev/loop0", "/snap/core/1", "squashfs", "ro"),
    Part("overlay", "/var/lib/demo/overlay", "overlay", "rw"),
    Part("server:/share", "/mnt/share", "nfs4", "rw"),
    Part("/dev/sdc1", "/media/demo/NEW", "ntfs3", "rw"),  # not in lsblk yet
]


def usage(mount, slow):
    if mount == "/mnt/share":
        return None  # hung network share
    return (100 * GB, 40 * GB, 60 * GB)


def by_mount(vols):
    return {str(v.mount_point.as_posix()): v for v in vols}


def test_parse_lsblk_inherits_disk_facts():
    d = parse_lsblk(json.dumps(LSBLK))
    assert d["/dev/sda1"]["tran"] == "usb" and d["/dev/sda1"]["removable"] is True
    assert d["/dev/sda1"]["model"] == "Demo Stick" and d["/dev/sda1"]["ro"] is False
    assert d["/"] is d["/dev/nvme0n1p2"]  # also keyed by mount point
    assert (
        d["/dev/nvme0n1p2"]["removable"] is False and d["/dev/nvme0n1p2"]["model"] == "Sample NVMe"
    )


@pytest.mark.parametrize("text", ["", "not json", "[]", '{"blockdevices": null}'])
def test_parse_lsblk_bad_input(text):
    assert parse_lsblk(text) == {}


def test_build_volumes():
    vols = build_volumes(PARTS, parse_lsblk(json.dumps(LSBLK)), usage)
    m = by_mount(vols)
    assert set(m) == {"/", "/boot/firmware", "/media/demo/STICK", "/media/demo/sd",
                      "/mnt/share", "/media/demo/NEW"}  # fmt: skip

    root = m["/"]
    assert root.is_system and not root.is_removable and root.label == "rootfs"
    assert root.device_name == "/dev/root" and root.fs_type == "ext4"  # matched by mount point
    assert (root.total, root.used, root.free) == (100 * GB, 40 * GB, 60 * GB)
    assert m["/boot/firmware"].is_system and m["/boot/firmware"].label == "bootfs"

    stick = m["/media/demo/STICK"]
    assert stick.is_removable and not stick.is_system and not stick.is_read_only
    assert stick.label == "STICK" and stick.id == "/dev/sda1" and stick.fs_type == "exfat"

    sd = m["/media/demo/sd"]
    assert sd.is_read_only and sd.is_removable and sd.label == "Card Reader"  # no fs label

    share = m["/mnt/share"]
    assert share.is_network and share.total == 0 and share.id == "nfs4:/mnt/share"

    new = m["/media/demo/NEW"]  # lsblk has not caught up: guessed from the mount location
    assert new.is_removable and new.label == "NEW"

    assert vols[0].is_system and vols[1].is_system  # system first, removable last
    assert [v.is_removable for v in vols] == sorted(v.is_removable for v in vols)
    assert len({v.id for v in vols}) == len(vols)


def test_build_volumes_without_lsblk():
    vols = build_volumes(PARTS[:4], {}, usage)
    m = by_mount(vols)
    assert m["/"].label == "/dev/root" and m["/"].is_system
    assert m["/media/demo/STICK"].is_removable and m["/media/demo/STICK"].label == "STICK"


def test_build_volumes_skips_unreadable_and_empty_local_mounts():
    parts = [
        Part("/dev/sda1", "/media/a", "vfat", "rw"),
        Part("/dev/sdb1", "/media/b", "vfat", "rw"),
    ]
    sizes = {"/media/a": None, "/media/b": (0, 0, 0)}
    assert build_volumes(parts, {}, lambda m, slow: sizes[m]) == []


def test_usage_slow_flag_only_for_network():
    calls = []

    def rec(m, slow):
        calls.append((m, slow))
        return (GB, 0, GB)

    build_volumes([PARTS[0], PARTS[9]], {}, rec)
    assert sorted(calls) == [("/", False), ("/mnt/share", True)]


def test_list_volumes_with_fake_psutil(monkeypatch):
    fake = types.SimpleNamespace(
        disk_partitions=lambda all: PARTS[:3],
        disk_usage=lambda m: types.SimpleNamespace(total=8 * GB, used=GB, free=7 * GB),
    )
    monkeypatch.setitem(sys.modules, "psutil", fake)
    monkeypatch.setattr(linux, "_run_lsblk", lambda: json.dumps(LSBLK))
    vols = LinuxPlatform().list_volumes()
    assert [v.mount_point.name for v in vols] == ["", "firmware", "STICK"]
    assert vols[2].total == 8 * GB and vols[2].is_removable


def test_list_volumes_never_raises(monkeypatch):
    def boom(all):
        raise PermissionError(errno.EACCES, "denied")

    monkeypatch.setitem(sys.modules, "psutil", types.SimpleNamespace(disk_partitions=boom))
    assert LinuxPlatform().list_volumes() == []


@pytest.mark.parametrize(
    "exc", [FileNotFoundError("lsblk"), subprocess.TimeoutExpired("lsblk", 3.0)]
)
def test_run_lsblk_failures(monkeypatch, exc):
    def run(*a, **kw):
        raise exc

    monkeypatch.setattr(linux.subprocess, "run", run)
    assert linux._run_lsblk() == ""


def test_run_lsblk_uses_timeout(monkeypatch):
    seen = {}

    def run(cmd, **kw):
        seen.update(kw, cmd=cmd)
        return types.SimpleNamespace(returncode=0, stdout="{}")

    monkeypatch.setattr(linux.subprocess, "run", run)
    assert linux._run_lsblk() == "{}"
    assert seen["timeout"] == linux.LSBLK_TIMEOUT and seen["cmd"][:2] == ["lsblk", "-J"]


def test_is_hidden():
    p = LinuxPlatform()
    assert p.is_hidden(Path("a") / ".cache") and not p.is_hidden(Path("a") / "notes.txt")


@POSIX_ONLY
def test_owner_and_perms(tmp_path):
    import pwd

    f = tmp_path / "f.txt"
    f.write_text("x")
    f.chmod(0o644)
    perms = LinuxPlatform().owner_and_perms(f)
    assert perms.owner == pwd.getpwuid(os.getuid()).pw_name
    assert perms.mode == "-rw-r--r--" and not perms.is_read_only and perms.group
    f.chmod(0o444)
    assert LinuxPlatform().owner_and_perms(f).is_read_only or IS_ROOT
    assert LinuxPlatform().owner_and_perms(tmp_path).mode.startswith("d")
    with pytest.raises(FileNotFoundError):
        LinuxPlatform().owner_and_perms(tmp_path / "missing")


def test_reveal(tmp_path, monkeypatch):
    calls, runs = [], []
    monkeypatch.setattr(linux.subprocess, "Popen", lambda cmd, **kw: calls.append((cmd, kw)))

    def no_file_manager(cmd, **kw):
        runs.append((cmd, kw))
        return types.SimpleNamespace(returncode=1)

    monkeypatch.setattr(linux.subprocess, "run", no_file_manager)
    f = tmp_path / "f.txt"
    f.write_text("x")
    p = LinuxPlatform()
    p.reveal(f)  # ShowItems fails -> open the parent folder
    p.reveal(tmp_path)  # folders open directly, no D-Bus call
    assert [c[0] for c in calls] == [["xdg-open", str(tmp_path)]] * 2
    assert calls[0][1]["start_new_session"] is True
    assert len(runs) == 1 and runs[0][1]["timeout"] == linux.REVEAL_TIMEOUT
    with pytest.raises(FileNotFoundError):
        p.reveal(tmp_path / "missing")


def test_reveal_selects_file_via_file_manager1(tmp_path, monkeypatch):
    calls, runs = [], []
    monkeypatch.setattr(linux.subprocess, "Popen", lambda cmd, **kw: calls.append(cmd))
    monkeypatch.setattr(
        linux.subprocess,
        "run",
        lambda cmd, **kw: runs.append(cmd) or types.SimpleNamespace(returncode=0),
    )
    f = tmp_path / "it's, odd.txt"
    f.write_text("x")
    LinuxPlatform().reveal(f)
    assert calls == []  # the file manager selected it; no fallback
    (cmd,) = runs
    assert cmd[0] == "gdbus" and f"{linux.FM1}.ShowItems" in cmd
    arg = cmd[-2]
    assert arg == f"['{f.as_uri()}']" and arg.count("'") == 2 and "," not in arg


@pytest.mark.parametrize("exc", [FileNotFoundError("gdbus"), subprocess.TimeoutExpired("gdbus", 2)])
def test_reveal_falls_back_without_gdbus(tmp_path, monkeypatch, exc):
    calls = []
    monkeypatch.setattr(linux.subprocess, "Popen", lambda cmd, **kw: calls.append(cmd))

    def run(cmd, **kw):
        raise exc

    monkeypatch.setattr(linux.subprocess, "run", run)
    f = tmp_path / "f.txt"
    f.write_text("x")
    LinuxPlatform().reveal(f)
    assert calls == [["xdg-open", str(tmp_path)]]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="statx is Linux only")
def test_created_birth_time(tmp_path):
    import time

    f = tmp_path / "f.txt"
    f.write_text("x")
    born = LinuxPlatform().created(f)
    # None where the filesystem (or an old kernel/libc) doesn't record birth time
    assert born is None or abs(born - time.time()) < 300
    if born is not None:
        with pytest.raises(FileNotFoundError):
            LinuxPlatform().created(tmp_path / "missing")


def test_created_without_statx(tmp_path, monkeypatch):
    monkeypatch.setattr(linux, "_libc_statx", lambda: None)
    assert LinuxPlatform().created(tmp_path) is None


def test_trash_without_trash_dir(tmp_path, monkeypatch):
    from somaari_disk.platform.base import TrashUnavailableError

    class TrashPermissionError(PermissionError):  # same name as send2trash's
        pass

    def fail(path):
        raise TrashPermissionError(13, "denied")

    monkeypatch.setitem(sys.modules, "send2trash", types.SimpleNamespace(send2trash=fail))
    f = tmp_path / "f"
    f.write_text("x")
    with pytest.raises(TrashUnavailableError):
        get_platform().trash(f)


@pytest.fixture
def fake_send2trash(monkeypatch):
    trashed = []
    mod = types.ModuleType("send2trash")
    mod.send2trash = trashed.append
    monkeypatch.setitem(sys.modules, "send2trash", mod)
    return trashed


def test_trash(tmp_path, fake_send2trash):
    f = tmp_path / "f.txt"
    f.write_text("x")
    get_platform().trash(f)
    assert fake_send2trash == [f]


def test_trash_refuses(tmp_path, fake_send2trash):
    p = get_platform()
    with pytest.raises(FileNotFoundError):
        p.trash(tmp_path / "missing")
    with pytest.raises(PermissionError):
        p.trash(Path(tmp_path.anchor))  # a volume root
    assert fake_send2trash == []


@POSIX_ONLY
@pytest.mark.skipif(IS_ROOT, reason="root ignores modes")
def test_trash_read_only_folder(tmp_path, fake_send2trash):
    d = tmp_path / "ro"
    d.mkdir()
    (d / "f").write_text("x")
    d.chmod(0o555)
    try:
        with pytest.raises(PermissionError):
            get_platform().trash(d / "f")
    finally:
        d.chmod(0o755)
    assert fake_send2trash == []


def test_trash_wraps_library_errors(tmp_path, monkeypatch):
    def fail(path):
        raise Exception("no trash dir")

    monkeypatch.setitem(sys.modules, "send2trash", types.SimpleNamespace(send2trash=fail))
    f = tmp_path / "f"
    f.write_text("x")
    with pytest.raises(OSError, match="no trash dir"):
        get_platform().trash(f)


def test_volume_id_uses_filesystem_uuid():
    tree = json.loads(json.dumps(LSBLK))
    stick = tree["blockdevices"][1]["children"][0]
    stick["uuid"] = "1A2B-3C4D"
    card = tree["blockdevices"][2]["children"][0]
    card["uuid"] = "1A2B-3C4D"  # a cloned card: same UUID, must not share the id
    m = by_mount(build_volumes(PARTS, parse_lsblk(json.dumps(tree)), usage))
    ids = {m["/media/demo/STICK"].id, m["/media/demo/sd"].id}
    assert len(ids) == 2 and "uuid:1A2B-3C4D" in ids  # the other falls back to its device
    assert ids & {"/dev/sda1", "/dev/sdb1"}
    assert m["/media/demo/NEW"].id == "/dev/sdc1"  # not in lsblk: no UUID known


def test_volume_id_survives_replug_to_new_device():
    def stick_on(dev, mount):
        dev_info = {"name": dev, "path": f"/dev/{dev}", "tran": "usb", "mountpoint": mount}
        tree = {"blockdevices": [{**dev_info, "uuid": "1A2B-3C4D", "rm": True}]}
        parts = [Part(f"/dev/{dev}", mount, "exfat", "rw")]
        return build_volumes(parts, parse_lsblk(json.dumps(tree)), usage)[0]

    first, again = stick_on("sda1", "/media/demo/STICK"), stick_on("sdb1", "/media/demo/STICK1")
    assert first.id == again.id == "uuid:1A2B-3C4D"
    assert first.device_name != again.device_name
