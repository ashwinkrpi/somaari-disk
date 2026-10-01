"""api.bridge on the fake platform: scan lifecycle, tree queries, actions, prefs, events."""

import json
import threading
import time
from pathlib import Path

import pytest

from somaari_disk.api.bridge import Api, usage_level
from somaari_disk.app import js_event_call, wire_window_events
from somaari_disk.core.models import Volume
from somaari_disk.core.tree import find_node, remove_node, sorted_children
from somaari_disk.platform.demo import demo_platform
from somaari_disk.platform.fake import FakeDir, FakeFile, FakePlatform

ROOT = Path("fake-root")
GB = 10**9


def vol(**kw):
    base = dict(
        id="dev-a",
        label="DATA",
        mount_point=ROOT,
        fs_type="exfat",
        total=100 * GB,
        used=40 * GB,
        free=60 * GB,
        is_removable=True,
    )
    return Volume(**{**base, **kw})


def sample_tree():
    return FakeDir(
        {
            "Videos": FakeDir({"a.mp4": FakeFile(5000), "b.mp4": FakeFile(3000)}),
            "Docs": FakeDir({"x.pdf": FakeFile(100), "Ünïcode ✓.txt": FakeFile(50)}),
            "zeta.bin": FakeFile(2000),
            "Alpha.txt": FakeFile(10),
            ".hidden": FakeFile(1),
        }
    )


class Recorder:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []
        self.done = threading.Event()

    def __call__(self, event, payload):
        json.dumps(payload)  # every event must be JSON-serializable
        self.events.append((event, payload))
        if event in ("scan_done", "error"):
            self.done.set()

    def of(self, kind):
        return [p for e, p in self.events if e == kind]


def make(tmp_path=None, tree=None, volumes=None, **kw):
    plat = FakePlatform(tree or sample_tree(), volumes=[vol()] if volumes is None else volumes)
    rec = Recorder()
    prefs = tmp_path / "cfg" / "prefs.json" if tmp_path else None
    api = Api(plat, rec, prefs_path=prefs, progress_interval=0.01, volume_interval=0.02, **kw)
    return api, plat, rec


def scanned(**kw):
    api, plat, rec = make(**kw)
    assert api.scan("dev-a")["ok"]
    assert rec.done.wait(5)
    return api, plat, rec


def assert_json(d):
    assert json.loads(json.dumps(d)) == d


# ---- tree helpers ------------------------------------------------------------


def test_sorted_children_orders_and_rolls_up():
    plat = FakePlatform(sample_tree())
    from somaari_disk.core.scanner import Scanner

    root = Scanner(plat).scan(ROOT).root
    top, rest = sorted_children(root, 3, "size")
    assert [c.name for c in top] == ["Videos", "zeta.bin", "Docs"]
    assert rest.count == 2 and rest.size == 11
    top, _ = sorted_children(root, 3, "name")
    assert [c.name for c in top] == ["Docs", "Videos", "zeta.bin"]  # same 3, by name
    with pytest.raises(ValueError):
        sorted_children(root, 3, "bogus")


def test_find_and_remove_node_updates_ancestors():
    from somaari_disk.core.scanner import Scanner

    root = Scanner(FakePlatform(sample_tree())).scan(ROOT).root
    total = root.size
    a = find_node(root, ("Videos", "a.mp4"))
    assert a is not None and a.size == 5000
    assert find_node(root, ("Videos", "nope")) is None
    assert find_node(root, ("zeta.bin", "child")) is None
    remove_node(a)
    assert root.size == total - 5000
    assert find_node(root, ("Videos",)).size == 3000
    with pytest.raises(ValueError):
        remove_node(root)


# ---- volumes + scan lifecycle ------------------------------------------------


def test_list_volumes_plain_dicts():
    api, _, _ = make(home=Path("home-dir"))
    res = api.list_volumes()
    assert_json(res)
    assert res["home"]["id"] == "home"
    v = res["volumes"][0]
    assert v["id"] == "dev-a" and v["is_removable"] and v["mount_segments"] == ["fake-root"]


def test_list_volumes_without_home():
    api, _, _ = make()
    assert api.list_volumes()["home"] is None


def test_scan_emits_progress_and_done():
    api, _, rec = scanned()
    done = rec.of("scan_done")[0]
    assert done["ok"] and not done["cancelled"] and not done["volume_lost"]
    assert done["files"] == 7 and done["size"] == 10161
    assert done["breadcrumb"] == [{"name": "fake-root", "segments": []}]
    assert rec.of("scan_progress"), "at least the final progress report"


def test_scan_home_location():
    plat = FakePlatform(sample_tree())
    rec = Recorder()
    api = Api(plat, rec, home=ROOT)
    assert api.scan("home")["ok"]
    assert rec.done.wait(5)
    assert rec.of("scan_done")[0]["ok"]


def test_scan_unknown_location():
    api, _, rec = make()
    res = api.scan("no-such-drive")
    assert res == {
        "ok": False,
        "error": {"code": "unknown_location", "message": res["error"]["message"]},
    }
    assert not rec.events


def test_scan_unreadable_root_reports_failure():
    api, _, rec = make(tree=FakeDir(stat_error=PermissionError))
    api.scan("dev-a")
    assert rec.done.wait(5)
    done = rec.of("scan_done")[0]
    assert not done["ok"] and done["error"]["code"] == "scan_failed"
    assert api.get_children([])["error"]["code"] == "no_scan"


def test_cancel_scan_and_new_scan_supersedes():
    wide = FakeDir(
        {f"d{i}": FakeDir({f"f{j}": FakeFile(10) for j in range(20)}) for i in range(300)}
    )
    api, plat, rec = make(tree=wide)
    first = api.scan("dev-a")["scan_id"]
    second = api.scan("dev-a")["scan_id"]
    assert second > first
    assert rec.done.wait(5)
    end = time.monotonic() + 5
    while not any(p["scan_id"] == second for p in rec.of("scan_done")) and time.monotonic() < end:
        time.sleep(0.01)
    ids = [p["scan_id"] for p in rec.of("scan_done")]
    assert ids == [second], "the superseded scan must not report"
    assert api.cancel_scan() == {"ok": True}


def test_cancel_running_scan():
    class Slow(FakePlatform):
        def scandir(self, path):
            time.sleep(0.01)
            return super().scandir(path)

    wide = FakeDir({f"d{i}": FakeDir({"f": FakeFile(1)}) for i in range(500)})
    rec = Recorder()
    api = Api(Slow(wide, volumes=[vol()]), rec, workers=1)
    api.scan("dev-a")
    time.sleep(0.05)
    api.cancel_scan()
    assert rec.done.wait(5)
    assert rec.of("scan_done")[0]["cancelled"]


def test_volume_lost_mid_scan():
    wide = FakeDir({f"d{i}": FakeDir({"f": FakeFile(1)}) for i in range(50)})
    plat = FakePlatform(wide, volumes=[vol()], vanish_after=5)
    rec = Recorder()
    api = Api(plat, rec)
    api.scan("dev-a")
    assert rec.done.wait(5)
    done = rec.of("scan_done")[0]
    assert done["volume_lost"] and done["warnings"].get("volume_lost") == 1


def test_scan_exception_becomes_error_event():
    class Broken(FakePlatform):
        def stat(self, path):
            raise RuntimeError("boom")

    rec = Recorder()
    api = Api(Broken(sample_tree(), volumes=[vol()]), rec)
    api.scan("dev-a")
    assert rec.done.wait(5)
    assert rec.of("error")[0]["code"] == "scan_failed"


def test_emitter_failure_does_not_break_scan():
    def bad_emit(event, payload):
        raise RuntimeError("window closed")

    plat = FakePlatform(sample_tree(), volumes=[vol()])
    api = Api(plat, bad_emit)
    api.scan("dev-a")
    end = time.monotonic() + 5
    # get_children is ok mid-scan too (partial tree), so wait for the finished one
    while api.get_children([]).get("partial") is not False and time.monotonic() < end:
        time.sleep(0.01)
    assert api.get_children([])["node"]["size"] == 10161


# ---- tree queries ----------------------------------------------------------------


def test_get_children_root():
    api, _, _ = scanned()
    res = api.get_children([], "size", 3)
    assert_json(res)
    assert res["is_root"] and res["node"]["size"] == 10161
    assert [c["name"] for c in res["children"]] == ["Videos", "zeta.bin", "Docs"]
    assert res["children"][0]["segments"] == ["Videos"]
    assert res["children"][0]["share_parent"] == pytest.approx(8000 / 10161)
    assert res["rest"] == {"count": 2, "size": 11, "size_on_disk": 8192}


def test_get_children_nested_breadcrumb_and_unicode():
    api, _, _ = scanned()
    res = api.get_children(["Docs"], "name")
    assert not res["is_root"]
    # casefold, then code point: "Ü" sorts after "x" (no locale collation in core)
    assert [c["name"] for c in res["children"]] == ["x.pdf", "Ünïcode ✓.txt"]
    assert res["breadcrumb"] == [
        {"name": "fake-root", "segments": []},
        {"name": "Docs", "segments": ["Docs"]},
    ]
    assert res["rest"] is None


def test_breadcrumb_includes_root_ancestors_as_not_navigable():
    deep = Path("outer") / "inner"
    plat = FakePlatform(sample_tree(), root=deep, volumes=[vol(mount_point=deep)])
    rec = Recorder()
    api = Api(plat, rec)
    api.scan("dev-a")
    assert rec.done.wait(5)
    crumbs = api.get_children(["Videos"])["breadcrumb"]
    assert crumbs == [
        {"name": "outer", "segments": None},
        {"name": "inner", "segments": []},
        {"name": "Videos", "segments": ["Videos"]},
    ]


def test_get_children_on_disk_and_errors():
    api, _, _ = scanned()
    res = api.get_children([], "size_on_disk", 1, True)
    assert res["children"][0]["name"] == "Videos"
    assert api.get_children(["zeta.bin"])["error"]["code"] == "not_a_folder"
    assert api.get_children(["missing"])["error"]["code"] == "not_found"
    assert api.get_children([], "bogus")["error"]["code"] == "bad_request"
    assert len(api.get_children([], "size", 0)["children"]) == 1  # limit clamped to >= 1


def test_get_children_before_scan():
    api, _, _ = make()
    assert api.get_children([])["error"]["code"] == "no_scan"


def test_get_details_file():
    api, _, _ = scanned()
    d = api.get_details(["Videos", "a.mp4"])
    assert_json(d)
    assert d["ok"] and not d["is_dir"] and d["size"] == 5000
    assert d["extension"] == "mp4"
    assert d["share_parent"] == pytest.approx(5000 / 8000)
    assert d["share_root"] == pytest.approx(5000 / 10161)
    assert d["owner"] == "user" and d["mode"] == "-rw-r--r--" and d["read_only"] is False
    assert d["path"] == str(ROOT / "Videos" / "a.mp4")
    assert d["hidden"] is False
    assert api.get_details([".hidden"])["hidden"] is True


def test_get_details_owner_unavailable():
    class NoPerms(FakePlatform):
        def owner_and_perms(self, path):
            raise PermissionError(13, "denied")

    rec = Recorder()
    api = Api(NoPerms(sample_tree(), volumes=[vol()]), rec)
    api.scan("dev-a")
    assert rec.done.wait(5)
    d = api.get_details(["Docs"])
    assert d["ok"] and d["owner"] is None and d["extension"] == ""


def test_get_usage():
    api, _, _ = scanned()
    u = api.get_usage()
    assert_json(u)
    assert u["volume_id"] == "dev-a" and u["percent"] == pytest.approx(40.0)
    assert make()[0].get_usage()["error"]["code"] == "no_scan"
    assert api.get_usage("dev-a")["total"] == 100 * GB
    assert u["level"] == "ok"
    assert u["whole_volume"] is True and u["scanned_size"] == 10161  # sample_tree total


def test_get_usage_scope_for_folder_scan():
    home = ROOT / "Docs"  # a folder on the drive, like the home folder
    plat = FakePlatform(sample_tree(), volumes=[vol()])
    rec = Recorder()
    api = Api(plat, rec, home=home)
    assert api.get_usage("home")["scanned_size"] is None  # nothing scanned yet
    api.scan("home")
    assert rec.done.wait(5)
    u = api.get_usage()
    assert u["whole_volume"] is False and u["scanned_size"] == 150
    assert api.get_usage("dev-a")["scanned_size"] is None  # not the shown location
    assert api.move_to_trash(["x.pdf"], True)["ok"]
    assert api.get_usage()["scanned_size"] == 50  # follows Move to Trash


@pytest.mark.parametrize(
    ("used", "free", "level"),
    [
        (0, 100, "ok"),
        (49, 51, "ok"),
        (50, 50, "filling"),
        (74, 26, "filling"),
        (75, 25, "low"),
        (99, 1, "low"),
        (100, 0, "full"),
        (95, 0, "full"),  # reserved blocks: no free space before 100% used
    ],
)
def test_usage_level_thresholds(used, free, level):
    assert usage_level(used * GB, 100 * GB, free * GB) == level


def test_usage_level_edges():
    assert usage_level(int(49.999 * GB), 100 * GB, 50 * GB) == "ok"
    assert usage_level(int(74.999 * GB), 100 * GB, 25 * GB) == "filling"
    assert usage_level(0, 0, 0) == "ok"  # unknown size: no warning


def test_get_usage_level_per_volume():
    api, _, _ = scanned(volumes=[vol(used=80 * GB, free=20 * GB)])
    assert api.get_usage()["level"] == "low"


class Gated(FakePlatform):
    """Listing any folder named in `gated` blocks until `gate` is set."""

    def __init__(self, *a, gated=(), **kw):
        super().__init__(*a, **kw)
        self.gate = threading.Event()
        self.gated = set(gated)

    def scandir(self, path):
        if path.name in self.gated:
            self.gate.wait(5)
        return super().scandir(path)


def wait_for(fn, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = fn()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("timed out")


def test_get_children_reads_partial_tree_while_scanning():
    plat = Gated(sample_tree(), volumes=[vol()], gated={"Docs"})
    rec = Recorder()
    api = Api(plat, rec, progress_interval=0.01)
    api.scan("dev-a")

    def videos_listed():
        res = api.get_children([])
        kids = {c["name"]: c for c in res.get("children", [])}
        return res if kids.get("Videos", {}).get("size") == 8000 else None

    res = wait_for(videos_listed)
    assert_json(res)
    assert res["partial"] is True
    docs = next(c for c in res["children"] if c["name"] == "Docs")
    assert docs["size"] == 0  # not listed yet
    assert api.get_details(["Videos", "a.mp4"])["size"] == 5000
    assert "Wait for the scan" in api.get_details(["Videos", "a.mp4"])["trash_blocked"]
    # actions wait for a finished scan: the tree is still being built
    assert api.move_to_trash(["zeta.bin"], confirmed=True)["error"]["code"] == "no_scan"
    assert api.open_in_file_manager(["Videos"])["error"]["code"] == "no_scan"
    plat.gate.set()
    assert rec.done.wait(5)
    res = api.get_children([])
    assert res["partial"] is False and res["node"]["size"] == 10161


def test_problem_flags_for_unreadable_folders():
    tree = FakeDir(
        {
            "Locked": FakeDir({"x": FakeFile(5)}, list_error=PermissionError),
            "Broken": FakeDir(list_error=OSError),
            "Mount": FakeDir({"y": FakeFile(9)}, dev=99),
            "ok.txt": FakeFile(3),
        }
    )
    api, _, rec = scanned(tree=tree)
    res = api.get_children([])
    flags = {c["name"]: c["problem"] for c in res["children"]}
    assert flags == {
        "Locked": "denied",
        "Broken": "unreadable",
        "Mount": "other_drive",
        "ok.txt": None,
    }
    assert res["node"]["problem"] is None
    locked = api.get_children(["Locked"])
    assert locked["node"]["problem"] == "denied" and locked["children"] == []
    assert api.get_details(["Locked"])["problem"] == "denied"
    assert rec.of("scan_done")[0]["warnings"]["permission"] == 1


def test_root_without_permission_is_flagged():
    api, _, rec = scanned(tree=FakeDir({"x": FakeFile(1)}, list_error=PermissionError))
    assert rec.of("scan_done")[0]["ok"]
    res = api.get_children([])
    assert res["node"]["problem"] == "denied" and res["children"] == []


def test_get_usage_picks_deepest_mount():
    outer = vol(id="outer", mount_point=Path("fake-root").parent)
    inner = vol(id="inner", used=90 * GB, free=10 * GB)
    api, _, _ = make(volumes=[outer, inner])
    assert api.get_usage("inner")["volume_id"] == "inner"


# ---- actions -------------------------------------------------------------------


def test_open_in_file_manager():
    api, plat, _ = scanned()
    assert api.open_in_file_manager(["Docs"]) == {"ok": True}
    assert plat.revealed == [ROOT / "Docs"]
    assert api.open_in_file_manager(["gone"])["error"]["code"] == "not_found"


def test_move_to_trash_needs_confirmation():
    api, plat, _ = scanned()
    assert api.move_to_trash(["zeta.bin"])["error"]["code"] == "not_confirmed"
    assert api.move_to_trash(["zeta.bin"], "yes")["error"]["code"] == "not_confirmed"
    assert api.move_to_trash([], True)["error"]["code"] == "bad_request"
    assert plat.trashed == []


def test_move_to_trash_updates_totals():
    api, plat, _ = scanned()
    res = api.move_to_trash(["Videos", "a.mp4"], True)
    assert_json(res)
    assert res["ok"] and res["removed"]["size"] == 5000
    assert res["parent"]["size"] == 3000 and res["root"]["size"] == 10161 - 5000
    assert plat.trashed == [ROOT / "Videos" / "a.mp4"]
    assert api.get_children(["Videos"])["node"]["child_count"] == 1
    assert api.get_details(["Videos", "a.mp4"])["error"]["code"] == "not_found"


def test_move_to_trash_read_only_volume():
    api, plat, _ = scanned(volumes=[vol(is_read_only=True)])
    res = api.move_to_trash(["zeta.bin"], True)
    assert res["error"]["code"] == "read_only"
    assert plat.trashed == []


def test_move_to_trash_permission_denied_keeps_tree():
    tree = sample_tree()
    tree.children["Docs"].read_only = True
    api, plat, _ = scanned(tree=tree)
    res = api.move_to_trash(["Docs", "x.pdf"], True)
    assert res["error"]["code"] == "permission"
    assert api.get_children(["Docs"])["node"]["child_count"] == 2


def test_get_details_file_view_fields():
    tree = sample_tree()
    tree.children["zeta.bin"].created = 1_600_000_000
    api, _, _ = scanned(tree=tree)
    d = api.get_details(["zeta.bin"])
    assert_json(d)
    assert d["created"] == 1_600_000_000 and d["size_on_disk"] == 4096
    assert d["read_only_drive"] is False and d["trash_blocked"] is None
    assert api.get_details(["Alpha.txt"])["created"] is None  # filesystem doesn't record it
    assert "can't be moved" in api.get_details([])["trash_blocked"]


def test_get_details_trash_blocked_on_read_only_drive():
    api, _, _ = scanned(volumes=[vol(is_read_only=True)])
    d = api.get_details(["zeta.bin"])
    assert d["read_only_drive"] is True and "read-only" in d["trash_blocked"]


def test_get_details_created_unreadable():
    class NoBirth(FakePlatform):
        def created(self, path):
            raise PermissionError(13, "denied")

    rec = Recorder()
    api = Api(NoBirth(sample_tree(), volumes=[vol()]), rec)
    api.scan("dev-a")
    assert rec.done.wait(5)
    assert api.get_details(["zeta.bin"])["created"] is None


def test_move_to_trash_no_trash_on_drive():
    from somaari_disk.platform.base import TrashUnavailableError

    api, plat, _ = scanned()
    plat.trash_error = TrashUnavailableError(13, "no trash", "x")
    res = api.move_to_trash(["zeta.bin"], True)
    assert res["error"]["code"] == "no_trash" and "Nothing was deleted" in res["error"]["message"]
    assert api.get_children([])["node"]["size"] == 10161  # tree untouched


def test_move_to_trash_already_gone_updates_tree():
    api, plat, _ = scanned()
    plat.trash_error = FileNotFoundError(2, "gone", "x")
    res = api.move_to_trash(["zeta.bin"], True)
    assert_json(res)
    assert res["error"]["code"] == "not_found" and "already gone" in res["error"]["message"]
    assert res["removed"]["size"] == 2000
    assert api.get_children([])["node"]["size"] == 10161 - 2000


def test_move_to_trash_plain_language_failures():
    import errno

    api, plat, _ = scanned()
    plat.trash_error = OSError(errno.EIO, "Input/output error")
    msg = api.move_to_trash(["zeta.bin"], True)["error"]["message"]
    assert msg == "Couldn't move this item to the Trash: Input/output error."
    plat.trash_error = OSError(errno.ENODEV, "No such device")
    assert api.move_to_trash(["zeta.bin"], True)["error"]["code"] == "drive_unavailable"
    plat.trash_error = OSError(errno.EROFS, "Read-only file system")  # remounted read-only
    res = api.move_to_trash(["zeta.bin"], True)
    assert res["error"]["code"] == "read_only" and "read-only" in res["error"]["message"]
    plat.trash_error = PermissionError(errno.EACCES, "denied")
    assert "permission" in api.move_to_trash(["zeta.bin"], True)["error"]["message"]
    assert plat.trashed == [] and api.get_children([])["node"]["size"] == 10161


def test_os_error_codes():
    import errno

    from somaari_disk.api.bridge import _os_error

    assert _os_error(OSError(errno.EROFS, "ro"))["error"]["code"] == "read_only"
    assert _os_error(FileNotFoundError(errno.ENOENT, "x"))["error"]["code"] == "not_found"
    assert _os_error(OSError(errno.EIO, "io"))["error"]["message"] == "io"


# ---- hot-plug + visibility ---------------------------------------------------------


def test_volumes_changed_only_while_visible():
    api, plat, rec = make()
    api.list_volumes()  # baseline
    plat.volumes.append(vol(id="dev-b", mount_point=Path("other")))
    time.sleep(0.1)
    assert not rec.of("volumes_changed"), "hidden window: no polling"
    api.set_visible(True)
    end = time.monotonic() + 3
    while not rec.of("volumes_changed") and time.monotonic() < end:
        time.sleep(0.01)
    ids = [v["id"] for v in rec.of("volumes_changed")[0]["volumes"]]
    assert ids == ["dev-a", "dev-b"]
    api.set_visible(False)
    assert api.shutdown() == {"ok": True}


# ---- prefs ---------------------------------------------------------------------------


def test_prefs_default_and_persist(tmp_path):
    api, _, _ = make(tmp_path)
    assert api.get_prefs() == {"ok": True, "theme": "system"}
    assert api.set_theme("dark") == {"ok": True, "theme": "dark"}
    again, _, _ = make(tmp_path)
    assert again.get_prefs()["theme"] == "dark"
    assert api.set_theme("neon")["error"]["code"] == "bad_request"


def test_prefs_corrupt_file(tmp_path):
    api, _, _ = make(tmp_path)
    path = tmp_path / "cfg" / "prefs.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    assert api.get_prefs()["theme"] == "system"
    path.write_text(json.dumps({"theme": "neon"}), encoding="utf-8")
    assert api.get_prefs()["theme"] == "system"


def test_prefs_without_path_is_memory_only():
    api, _, _ = make()
    assert api.set_theme("light")["ok"]


# ---- app glue ------------------------------------------------------------------------


def test_js_event_call_escapes_payload():
    js = js_event_call("scan_done", {"name": "</script> \"'"})
    assert js.startswith('window.duaEvent && window.duaEvent("scan_done", {')
    assert " " not in js


class FakeEvent:
    """Mimics pywebview's Event: += adds a handler; fire() collects results in a set."""

    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def fire(self):
        return {h() for h in self.handlers}


class FakeWindowEvents:
    def __init__(self):
        self.shown, self.restored = FakeEvent(), FakeEvent()
        self.minimized, self.closed = FakeEvent(), FakeEvent()


def test_window_event_handlers_return_hashable_none():
    calls = []

    class SpyApi:
        def set_visible(self, visible):
            calls.append(("visible", visible))
            return {"ok": True}

        def shutdown(self):
            calls.append(("shutdown",))
            return {"ok": True}

    events = FakeWindowEvents()
    wire_window_events(events, SpyApi())
    for name in ("shown", "minimized", "restored", "closed"):
        assert getattr(events, name).fire() == {None}
    assert calls == [("visible", True), ("visible", False), ("visible", True), ("shutdown",)]


def test_demo_platform_scans():
    plat = demo_platform()
    rec = Recorder()
    api = Api(plat, rec)
    vols = api.list_volumes()["volumes"]
    assert [v["id"] for v in vols] == ["demo-internal", "demo-usb"]
    api.scan("demo-usb")
    assert rec.done.wait(5)
    done = rec.of("scan_done")[0]
    assert done["ok"] and done["files"] > 0 and not done["warnings"]
    assert api.get_children([])["children"][0]["name"] == "Photos"


def test_guarded_methods_keep_signatures_for_pywebview():
    import inspect

    assert inspect.getfullargspec(Api.get_children).args == [
        "self",
        "segments",
        "sort",
        "limit",
        "on_disk",
    ]
    assert inspect.getfullargspec(Api.move_to_trash).args == ["self", "segments", "confirmed"]
