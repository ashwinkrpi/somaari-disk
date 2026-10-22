"""Fake platform ops, hot-plug watcher, volume lost mid-scan, `volumes` CLI."""

import errno
import io
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from somaari_disk.cli import main as scan_main
from somaari_disk.cli import volumes_main
from somaari_disk.core.models import Volume
from somaari_disk.core.scanner import Scanner
from somaari_disk.core.volumes import VolumeWatcher
from somaari_disk.platform import get_platform
from somaari_disk.platform.base import EntryInfo, Kind
from somaari_disk.platform.fake import FakeDir, FakeFile, FakePlatform

ROOT = Path("fake-root")
GB = 10**9


def vol(name, **kw):
    base = dict(
        id=f"dev-{name}",
        label=name.upper(),
        mount_point=Path("mnt") / name,
        fs_type="exfat",
        total=64 * GB,
        used=GB,
        free=63 * GB,
        is_removable=True,
    )
    return Volume(**{**base, **kw})


def wide_tree(dirs=50, files=5):
    return FakeDir(
        {f"d{i}": FakeDir({f"f{j}": FakeFile(100) for j in range(files)}) for i in range(dirs)}
    )


def wait_for(cond, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.005)
    return cond()


# --- fake platform ops ---


def test_fake_trash_and_reveal():
    tree = FakeDir({"a": FakeDir({"x.bin": FakeFile(10)}), ".hidden": FakeFile(1)})
    p = FakePlatform(tree, ROOT)
    p.trash(ROOT / "a" / "x.bin")
    assert p.trashed == [ROOT / "a" / "x.bin"] and tree.children["a"].children == {}
    p.reveal(ROOT / "a")
    assert p.revealed == [ROOT / "a"]
    assert p.is_hidden(ROOT / ".hidden") and not p.is_hidden(ROOT / "a")
    with pytest.raises(FileNotFoundError):
        p.trash(ROOT / "a" / "x.bin")
    with pytest.raises(PermissionError):
        p.trash(ROOT)


def test_fake_trash_read_only_parent():
    tree = FakeDir({"ro": FakeDir({"f": FakeFile(1)}, read_only=True)})
    p = FakePlatform(tree, ROOT)
    with pytest.raises(PermissionError):
        p.trash(ROOT / "ro" / "f")
    assert p.trashed == [] and "f" in tree.children["ro"].children


def test_fake_owner_and_perms():
    tree = FakeDir({"f": FakeFile(1, owner="demo", read_only=True), "d": FakeDir()})
    p = FakePlatform(tree, ROOT)
    f = p.owner_and_perms(ROOT / "f")
    assert (f.owner, f.mode, f.is_read_only) == ("demo", "-r--r--r--", True)
    assert p.owner_and_perms(ROOT / "d").mode == "drwxr-xr-x"


def test_get_platform_matches_os():
    name = type(get_platform()).__name__
    assert name == ("WindowsPlatform" if sys.platform == "win32" else "LinuxPlatform")


# --- volume lost mid-scan ---


def test_volume_vanishes_mid_scan():
    p = FakePlatform(wide_tree(), ROOT, vanish_after=5)
    r = Scanner(p, workers=2).scan(ROOT)
    assert r.volume_lost and not r.cancelled
    assert [w.kind for w in r.warnings].count("volume_lost") == 1
    assert 0 < r.files < 250  # partial tree, still summed
    assert r.root.size == sum(c.size for c in r.root.children)
    assert p.list_volumes() == []


def test_folder_error_is_not_volume_lost():
    tree = wide_tree(3)
    tree.children["d1"].list_error = OSError  # EIO on one folder, volume still there
    r = Scanner(FakePlatform(tree, ROOT)).scan(ROOT)
    assert not r.volume_lost and [w.kind for w in r.warnings] == ["error"]
    assert r.dirs == 4


def test_entry_info_is_immutable():
    info = EntryInfo("a.txt", Kind.FILE, 10, 4096, 1, 7)
    with pytest.raises(AttributeError):
        info.size = 0  # type: ignore[misc]
    assert info._replace(dev=99).dev == 99 and info.dev == 7
    assert (info.file_id, EntryInfo("d", Kind.DIR).size) == (None, 0)


def test_remount_detected_by_device_change():
    """Desktop auto-unmount: the mount point now shows the parent filesystem."""

    class Unmounted(FakePlatform):
        calls = 0

        def stat(self, path):
            self.calls += 1
            info = super().stat(path)
            return info._replace(dev=99) if self.calls > 1 else info

    tree = wide_tree(3)
    tree.children["d1"].list_error = OSError
    r = Scanner(Unmounted(tree, ROOT)).scan(ROOT)
    assert r.volume_lost


def test_device_gone_errno_is_volume_lost():
    import errno

    class Gone(FakePlatform):
        def scandir(self, path):
            if path != ROOT:
                raise OSError(errno.ENODEV, "No such device", str(path))
            return super().scandir(path)

    r = Scanner(Gone(wide_tree(3), ROOT)).scan(ROOT)
    assert r.volume_lost and isinstance(r.root.children[0].size, int)


def test_scan_cli_reports_volume_lost(capsys):
    p = FakePlatform(wide_tree(), ROOT, vanish_after=3)
    assert scan_main([str(ROOT), "--quiet"], platform=p) == 2
    assert "disconnected" in capsys.readouterr().out


# --- hot-plug watcher ---


class CountingPlatform(FakePlatform):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.polls = 0

    def list_volumes(self):
        self.polls += 1
        return super().list_volumes()


def test_watcher_starts_paused_and_detects_plug_and_unplug():
    p = CountingPlatform(FakeDir(), ROOT, volumes=[vol("sys", is_removable=False)])
    events: list[list[Volume]] = []
    w = VolumeWatcher(p, events.append, interval=0.01)
    assert [v.label for v in w.snapshot()] == ["SYS"]
    time.sleep(0.05)
    assert p.polls == 1 and not w.polling  # paused: no thread, no polls
    try:
        w.resume()
        p.volumes.append(vol("stick"))
        assert wait_for(lambda: len(events) == 1)
        assert [v.label for v in events[0]] == ["SYS", "STICK"]
        p.volumes.pop()
        assert wait_for(lambda: len(events) == 2)
        assert [v.label for v in events[1]] == ["SYS"]
    finally:
        w.stop()


def test_watcher_ignores_free_space_changes():
    p = FakePlatform(FakeDir(), ROOT, volumes=[vol("stick")])
    events = []
    w = VolumeWatcher(p, events.append, interval=0.01)
    w.snapshot()
    w.resume()
    try:
        p.volumes[0] = replace(p.volumes[0], used=2 * GB, free=62 * GB)
        time.sleep(0.1)
        assert events == []
        p.volumes[0] = replace(p.volumes[0], is_read_only=True)  # remounted read-only
        assert wait_for(lambda: len(events) == 1)
    finally:
        w.stop()


def test_watcher_pause_stops_polling():
    p = CountingPlatform(FakeDir(), ROOT)
    w = VolumeWatcher(p, lambda v: None, interval=0.01)
    w.resume()
    try:
        assert wait_for(lambda: p.polls >= 3)
        w.pause()
        time.sleep(0.05)  # let an in-flight poll finish
        n = p.polls
        time.sleep(0.1)
        assert p.polls == n and not w.polling
        w.resume()
        assert wait_for(lambda: p.polls > n)
    finally:
        w.stop()
    assert not any(t.name == "volumes" and t.is_alive() for t in threading.enumerate())


def test_watcher_survives_errors():
    class Flaky(FakePlatform):
        n = 0

        def list_volumes(self):
            self.n += 1
            if self.n % 2:
                raise RuntimeError("boom")
            return [vol("stick")]

    def bad_listener(vols):
        raise ValueError("listener bug")

    p = Flaky(FakeDir(), ROOT)
    w = VolumeWatcher(p, bad_listener, interval=0.01)
    w.resume()
    try:
        assert wait_for(lambda: p.n >= 6)
    finally:
        w.stop()


def test_stopped_watcher_does_not_restart():
    p = CountingPlatform(FakeDir(), ROOT)
    w = VolumeWatcher(p, lambda v: None, interval=0.01)
    w.stop()
    w.resume()
    time.sleep(0.05)
    assert p.polls == 0


# --- volumes CLI ---


def test_volumes_cli():
    p = FakePlatform(
        FakeDir(),
        ROOT,
        volumes=[
            vol("sys", is_removable=False, is_system=True, fs_type="ext4"),
            vol("stick", is_read_only=True, device_name="dev-sample"),
        ],
    )
    out = io.StringIO()
    assert volumes_main([], platform=p, out=out) == 0
    lines = out.getvalue().splitlines()
    assert len(lines) == 2
    assert "ext4" in lines[0] and "system" in lines[0] and "64.0 GB" in lines[0]
    assert "[STICK]" in lines[1] and "removable read-only" in lines[1]


def test_watcher_sees_unmounted_card_and_its_mount():
    card = vol("card", is_mounted=False, mount_point=Path(), device_name="/dev/sdx1")
    p = FakePlatform(FakeDir(), ROOT, volumes=[vol("sys", is_removable=False)])
    events: list[list[Volume]] = []
    w = VolumeWatcher(p, events.append, interval=0.01)
    w.snapshot()
    try:
        w.resume()
        p.unmounted.append(card)  # card inserted, nothing mounted it
        assert wait_for(lambda: len(events) == 1)
        assert [(v.label, v.is_mounted) for v in events[0]] == [("SYS", True), ("CARD", False)]
        p.mount("/dev/sdx1")
        assert wait_for(lambda: len(events) == 2)
        assert [(v.label, v.is_mounted) for v in events[1]] == [("SYS", True), ("CARD", True)]
    finally:
        w.stop()


def test_base_platform_cannot_mount():
    from somaari_disk.platform.base import Platform

    with pytest.raises(OSError) as e:
        Platform.mount(FakePlatform(FakeDir()), "/dev/sdx1")
    assert e.value.errno == errno.ENOTSUP
