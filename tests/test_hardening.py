"""Stage 6: saved scans, app log, odd names, unplug mid-scan, bounded scan memory, bench."""

import gzip
import io
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from somaari_disk import cli
from somaari_disk.api.bridge import Api
from somaari_disk.api.scan_store import ScanStore
from somaari_disk.core import cache
from somaari_disk.core import scanner as scanner_mod
from somaari_disk.core.models import Node, ScanResult, ScanWarning
from somaari_disk.core.scanner import Scanner
from somaari_disk.core.tree import display_name, find_node, iter_nodes, path_segments
from somaari_disk.logsetup import LOG_NAME, setup_logging
from somaari_disk.platform import get_platform
from somaari_disk.platform.fake import FakeDir, FakeFile, FakePlatform
from test_api import Recorder, make, sample_tree

BAD = "bad-\udcff-name"  # how the OS hands Python a name that is not valid UTF-8
POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="POSIX byte names")


def odd_tree():
    return FakeDir(
        {
            "with space": FakeDir({"emoji \U0001f4e6.txt": FakeFile(70)}),
            BAD: FakeFile(30),
            "x" * 255: FakeFile(5),
            "line\nbreak": FakeFile(2),
            "same": FakeDir({"a": FakeFile(1, file_id=9), "b": FakeFile(1, file_id=9)}),
            "locked": FakeDir(list_error=PermissionError),
        }
    )


def fake_scan(tree=None) -> ScanResult:
    plat = FakePlatform(tree or odd_tree())
    return Scanner(plat).scan(plat.root)


def shape(node: Node) -> list:
    return [
        node.name,
        node.is_dir,
        node.size,
        node.size_on_disk,
        node.mtime,
        [shape(c) for c in node.children or ()],
    ]


def roundtrip(result: ScanResult, scanned_at: int = 1_700_000_000) -> tuple[ScanResult, int]:
    buf = io.StringIO()
    cache.dump(result, buf, scanned_at)
    buf.getvalue().encode("ascii")  # surrogates are escaped, so any encoding can store it
    return cache.load(io.StringIO(buf.getvalue()))


@pytest.fixture
def app_logger():
    logger = logging.getLogger("somaari_disk")
    before, level = list(logger.handlers), logger.level
    yield logger
    for h in logger.handlers:
        if h not in before:
            logger.removeHandler(h)
            h.close()
    logger.setLevel(level)


# ---- core/cache ---------------------------------------------------------------


def test_cache_roundtrip_keeps_tree_counts_and_warnings():
    result = fake_scan()
    back, at = roundtrip(result)
    assert at == 1_700_000_000
    assert shape(back.root) == shape(result.root)
    assert back.root_path == result.root_path
    assert back.warnings == result.warnings and any(w.kind == "permission" for w in back.warnings)
    for k in ("files", "dirs", "links_skipped", "hardlinks_deduped"):
        assert getattr(back, k) == getattr(result, k)
    assert back.hardlinks_deduped == 1
    for node in iter_nodes(back.root):  # parent links rebuilt
        for c in node.children or ():
            assert c.parent is node
    assert find_node(back.root, [BAD]) is not None


def test_cache_roundtrip_single_file_and_empty_dir():
    lone = ScanResult(Node("f.bin", False, 10, 4096, 5), Path("f.bin"), files=1)
    assert shape(roundtrip(lone)[0].root) == shape(lone.root)
    empty = ScanResult(Node("e", True), Path("e"), dirs=1)
    back = roundtrip(empty)[0]
    assert back.root.is_dir and back.root.children == []


def test_cache_shares_repeated_names():
    tree = FakeDir({f"d{i}": FakeDir({"index.js": FakeFile(1)}) for i in range(3)})
    back, _ = roundtrip(fake_scan(tree))
    names = [c.children[0].name for c in back.root.children]
    assert names[0] is names[1] is names[2]


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not json\n",
        '{"format": 99}\n"end"\n',
        '["header is not a dict"]\n',
        '{"format": 1, "root_path": [], "warnings": []}\n[0, "r", 1, 0, 0, 0]\n',  # no "end"
        '{"format": 1, "root_path": [], "warnings": []}\n"end"\n',  # no root
        '{"format": 1, "root_path": [], "warnings": []}\n[1, "x", 0, 0, 0, 0]\n"end"\n',
        '{"format": 1, "root_path": [], "warnings": []}\n[0, "r", 1, 0, 0, 0]\n'
        '[0, "r2", 1, 0, 0, 0]\n"end"\n',  # two roots
        '{"format": 1, "root_path": [], "warnings": []}\n[0, "r", 1, 0, 0, 0]\n'
        '[3, "deep", 0, 0, 0, 0]\n"end"\n',  # skips a level
        '{"format": 1, "root_path": [], "warnings": []}\n[0, "r", 1]\n"end"\n',
    ],
)
def test_cache_load_rejects_broken_input(text):
    with pytest.raises(cache.CacheError):
        cache.load(io.StringIO(text))


def test_cache_load_rejects_truncated_dump():
    buf = io.StringIO()
    cache.dump(fake_scan(), buf, 0)
    lines = buf.getvalue().splitlines(keepends=True)
    with pytest.raises(cache.CacheError):
        cache.load(io.StringIO("".join(lines[:-2])))


# ---- api/scan_store -------------------------------------------------------------


def test_store_save_load_gzip(tmp_path):
    store = ScanStore(tmp_path / "scans")
    assert store.load("uuid:1A2B") is None and not store.has("uuid:1A2B")
    result = fake_scan()
    assert store.save("uuid:1A2B", result, 123)
    path = store.path_for("uuid:1A2B")
    assert path.parent == tmp_path / "scans" and path.name.endswith(".jsonl.gz")
    with gzip.open(path, "rt", encoding="ascii") as fp:
        assert json.loads(fp.readline())["format"] == cache.FORMAT
    assert [p.name for p in path.parent.iterdir()] == [path.name]  # no temp file left
    back, at = store.load("uuid:1A2B")
    assert at == 123 and shape(back.root) == shape(result.root)


def test_store_path_is_safe_for_any_id(tmp_path):
    store = ScanStore(tmp_path)
    for loc in ("/dev/sda1", "nfs4:/mnt/share", "home", "..", "uuid:\udcff", "C:\\"):
        p = store.path_for(loc)
        assert p.parent == tmp_path and "/" not in p.name and "\\" not in p.name
    assert store.path_for("a") != store.path_for("b")


@pytest.mark.parametrize("damage", [b"", b"not gzip at all", "gzip-cut", "gzip-garbage"], ids=str)
def test_store_load_removes_broken_file(tmp_path, damage):
    store = ScanStore(tmp_path)
    store.save("loc", fake_scan(), 1)
    path = store.path_for("loc")
    if damage == "gzip-cut":
        data = path.read_bytes()
        path.write_bytes(data[: len(data) // 2])
    elif damage == "gzip-garbage":
        path.write_bytes(gzip.compress(b"\xff\xfe nonsense\n"))
    else:
        path.write_bytes(damage)
    assert store.load("loc") is None
    assert not path.exists()


def test_store_save_failure_returns_false(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert ScanStore(blocker / "scans").save("loc", fake_scan(), 1) is False


def test_store_prunes_oldest(tmp_path):
    store = ScanStore(tmp_path, keep=3)
    result = fake_scan()
    for i in range(5):
        store.save(f"loc{i}", result, i)
        os.utime(store.path_for(f"loc{i}"), (1000 + i, 1000 + i))
    kept = {p.name for p in tmp_path.iterdir()}
    assert kept == {store.path_for(f"loc{i}").name for i in (2, 3, 4)}


# ---- api with a cache dir ---------------------------------------------------------


def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def cached_api(tmp_path, tree=None, **kw):
    return make(tree=tree, cache_dir=tmp_path / "scans", **kw)


def test_api_saves_scan_and_reopens_it(tmp_path):
    api, plat, rec = cached_api(tmp_path)
    res = api.scan("dev-a", True)  # nothing saved yet: a real scan
    assert res["ok"] and res["cached"] is False
    assert rec.done.wait(5)
    done = rec.of("scan_done")[-1]
    assert done["cached"] is False and isinstance(done["scanned_at"], int)
    store = ScanStore(tmp_path / "scans")
    assert wait_for(lambda: store.has("dev-a"))
    live = api.get_children([])

    api2, _, rec2 = cached_api(tmp_path)
    res = api2.scan("dev-a", True)
    assert res["cached"] is True
    assert rec2.done.wait(5)
    done2 = rec2.of("scan_done")[-1]
    assert done2["ok"] and done2["cached"] is True
    assert done2["scanned_at"] == done["scanned_at"]
    assert done2["files"] == done["files"] and done2["size"] == done["size"]
    assert not rec2.of("scan_progress")  # nothing was scanned
    got = api2.get_children([])
    assert got["children"] == live["children"] and got["node"] == live["node"]


def test_api_rescan_ignores_cache(tmp_path):
    api, plat, rec = cached_api(tmp_path)
    api.scan("dev-a")
    assert rec.done.wait(5)
    assert wait_for(lambda: ScanStore(tmp_path / "scans").has("dev-a"))
    rec.done.clear()
    assert api.scan("dev-a")["cached"] is False  # use_cache defaults to off
    assert rec.done.wait(5)
    assert rec.of("scan_done")[-1]["cached"] is False


def test_api_without_cache_dir_never_caches(tmp_path):
    api, plat, rec = make()
    assert api.scan("dev-a", True)["cached"] is False
    assert rec.done.wait(5)
    assert rec.of("scan_done")[-1]["ok"]


@pytest.mark.parametrize("how", ["cancelled", "volume_lost"])
def test_api_does_not_save_partial_scans(tmp_path, how):
    store = ScanStore(tmp_path / "scans")
    if how == "volume_lost":
        api, plat, rec = cached_api(tmp_path)
        plat._vanish_after = 1
        api.scan("dev-a")
        assert rec.done.wait(5)
        assert rec.of("scan_done")[-1]["volume_lost"]
    else:
        gate = threading.Event()
        api, plat, rec = cached_api(tmp_path)
        orig = plat.scandir

        def slow(path):
            gate.wait(5)
            return orig(path)

        plat.scandir = slow
        api.scan("dev-a")
        api.cancel_scan()
        gate.set()
        assert rec.done.wait(5)
        assert rec.of("scan_done")[-1]["cancelled"]
    time.sleep(0.1)
    assert not store.has("dev-a")


def test_api_broken_cache_falls_back_to_scan(tmp_path):
    store = ScanStore(tmp_path / "scans")
    store.directory.mkdir()
    store.path_for("dev-a").write_bytes(b"garbage")
    api, plat, rec = cached_api(tmp_path)
    assert api.scan("dev-a", True)["cached"] is True  # a file is there; it turns out broken
    assert rec.done.wait(5)
    done = rec.of("scan_done")[-1]
    assert done["ok"] and done["cached"] is False and done["files"] > 0
    assert wait_for(lambda: store.load("dev-a") is not None)  # replaced by the fresh scan


def test_api_cached_scan_uses_current_mount_point(tmp_path):
    api, plat, rec = cached_api(tmp_path)
    api.scan("dev-a")
    assert rec.done.wait(5)
    assert wait_for(lambda: ScanStore(tmp_path / "scans").has("dev-a"))

    from test_api import vol

    moved = Path("fake-root-1")  # same drive, mounted elsewhere after a replug
    plat2 = FakePlatform(sample_tree(), root=moved, volumes=[vol(mount_point=moved)])
    rec2 = Recorder()
    api2 = Api(plat2, rec2, cache_dir=tmp_path / "scans")
    api2.scan("dev-a", True)
    assert rec2.done.wait(5) and rec2.of("scan_done")[-1]["cached"]
    crumbs = rec2.of("scan_done")[-1]["breadcrumb"]
    assert crumbs[-1]["name"] == "fake-root-1"
    assert api2.move_to_trash(["zeta.bin"], True)["ok"]
    assert plat2.trashed == [moved / "zeta.bin"]


def test_api_trash_updates_saved_scan(tmp_path):
    api, plat, rec = cached_api(tmp_path)
    api.scan("dev-a")
    assert rec.done.wait(5)
    store = ScanStore(tmp_path / "scans")
    assert wait_for(lambda: store.has("dev-a"))
    assert api.move_to_trash(["Videos", "a.mp4"], True)["ok"]

    def saved_without_it():
        loaded = store.load("dev-a")
        return loaded is not None and find_node(loaded[0].root, ["Videos", "a.mp4"]) is None

    assert wait_for(saved_without_it)
    assert store.load("dev-a")[0].root.size == api.get_children([])["node"]["size"]


def test_api_unexpected_error_is_logged_and_reported(tmp_path, app_logger):
    setup_logging(tmp_path / "logs", Path.home())
    api, plat, rec = scanned_ok()

    def boom(path):
        raise RuntimeError("kaboom")

    plat.reveal = boom
    res = api.open_in_file_manager(["zeta.bin"])
    assert res == {
        "ok": False,
        "error": {
            "code": "internal",
            "message": "Something went wrong. Details are in the app log.",
        },
    }
    log = (tmp_path / "logs" / LOG_NAME).read_text(encoding="utf-8")
    assert "open_in_file_manager failed" in log and "RuntimeError: kaboom" in log


def scanned_ok():
    api, plat, rec = make()
    api.scan("dev-a")
    assert rec.done.wait(5)
    return api, plat, rec


# ---- app log --------------------------------------------------------------------


def test_log_redacts_home_everywhere(tmp_path, app_logger):
    home = tmp_path / "home" / "someone"
    path = setup_logging(tmp_path / "logs", home)
    assert path == tmp_path / "logs" / LOG_NAME
    log = logging.getLogger("somaari_disk.test")
    log.info("opened %s", home / "Videos" / "clip.mp4")
    try:
        raise OSError(2, "gone", str(home / "x"))
    except OSError:
        log.exception("failed")
    log.info("bad name %s", BAD)  # must not raise on a non-UTF-8 name
    text = path.read_text(encoding="utf-8")
    assert str(home) not in text and "someone" not in text
    assert "~/Videos/clip.mp4".replace("/", os.sep) in text or "~/Videos/clip.mp4" in text
    assert "start: python" in text
    assert "bad-\\udcff-name" in text


def test_log_root_home_is_not_blanked(tmp_path, app_logger):
    path = setup_logging(tmp_path / "logs", Path("/"))
    logging.getLogger("somaari_disk.test").info("a/b")
    assert "a/b" in path.read_text(encoding="utf-8")


def test_log_rotates(tmp_path, app_logger, monkeypatch):
    from somaari_disk import logsetup

    monkeypatch.setattr(logsetup, "MAX_BYTES", 2000)
    path = setup_logging(tmp_path / "logs", Path.home())
    log = logging.getLogger("somaari_disk.test")
    for i in range(400):
        log.info("line %d %s", i, "x" * 50)
    files = sorted(p.name for p in path.parent.iterdir())
    assert files == [LOG_NAME, *(f"{LOG_NAME}.{i}" for i in range(1, logsetup.BACKUPS + 1))]
    assert all(p.stat().st_size <= 2200 for p in path.parent.iterdir())


def test_log_setup_twice_keeps_one_handler(tmp_path, app_logger):
    setup_logging(tmp_path / "a", Path.home())
    setup_logging(tmp_path / "b", Path.home())
    files = [h for h in app_logger.handlers if isinstance(h, logging.FileHandler)]
    assert len(files) == 1


def test_log_unwritable_dir_is_not_fatal(tmp_path, app_logger):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert setup_logging(blocker / "logs", Path.home()) is None


def test_scan_log_has_no_paths(tmp_path, app_logger):
    home = Path("fake-root")  # the fake platform's root plays the home folder
    path = setup_logging(tmp_path / "logs", tmp_path / "unused-home")
    plat = FakePlatform(odd_tree(), root=home)
    rec = Recorder()
    api = Api(plat, rec, home=home, cache_dir=tmp_path / "scans")
    api.scan("home")
    assert rec.done.wait(5)
    assert wait_for(lambda: "cache saved" in path.read_text(encoding="utf-8"))
    text = path.read_text(encoding="utf-8")
    assert "scan 1 start: home" in text and "scan 1 done: files=" in text
    for name in ("fake-root", "with space", "emoji", "locked", "bad-"):
        assert name not in text


# ---- odd names --------------------------------------------------------------------


def test_display_name():
    assert display_name("emoji \U0001f4e6 ok") == "emoji \U0001f4e6 ok"
    assert display_name(BAD) == "bad-\ufffd-name"
    display_name(BAD).encode("utf-8")


def test_find_node_accepts_replacement_char_for_bad_bytes():
    root = fake_scan().root
    assert find_node(root, [BAD]).name == BAD
    assert find_node(root, ["bad-\ufffd-name"]).name == BAD  # as the UI may send it back
    # chr(): ruff reads both surrogate literals as U+FFFD and flags a repeated key
    two = fake_scan(FakeDir({"a" + chr(0xDCFF): FakeFile(1), "a" + chr(0xDCFE): FakeFile(2)})).root
    assert find_node(two, ["a\ufffd"]) is None  # ambiguous: refuse rather than guess
    assert find_node(two, ["a" + chr(0xDCFE)]).size == 2


def test_api_round_trips_odd_names_through_json():
    api, plat, rec = make(tree=odd_tree())
    api.scan("dev-a")
    assert rec.done.wait(5)
    listing = json.loads(json.dumps(api.get_children([], "size", 100)))
    names = {c["name"] for c in listing["children"]}
    assert {"with space", BAD, "x" * 255, "line\nbreak"} <= names
    for child in listing["children"]:
        segs = json.loads(json.dumps(child["segments"]))
        assert api.get_details(segs)["ok"], child["name"]
    inner = api.get_children(["with space"])
    assert inner["children"][0]["name"] == "emoji \U0001f4e6.txt"
    assert [c["name"] for c in inner["breadcrumb"]][-1] == "with space"
    assert api.move_to_trash([BAD], True)["ok"]
    assert plat.trashed == [plat.root / BAD]


def test_cli_report_prints_bad_names_on_strict_utf8():
    out = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="strict")
    result = fake_scan()
    cli.print_report(result, top=20, files=20, by="size", out=out)
    out.flush()
    text = out.buffer.getvalue().decode("utf-8")
    assert "bad-\ufffd-name" in text and "emoji \U0001f4e6.txt" in text


def make_odd_files(base: Path) -> list[str]:
    """Real odd names in tmp_path; returns those the filesystem accepted."""
    made = []
    names = ["with space", "emoji \U0001f4e6", "x" * 255, "-dash"]
    if sys.platform != "win32":
        names.append("trailing space ")  # Windows silently drops trailing spaces
    for name in names:
        try:
            (base / name).write_bytes(b"12345")
            made.append(name)
        except OSError:
            pass
    return made


def test_local_scan_odd_names(tmp_path):
    made = make_odd_files(tmp_path)
    assert "with space" in made
    plat = get_platform()
    result = Scanner(plat).scan(tmp_path)
    names = {c.name for c in result.root.children}
    assert set(made) <= names
    assert result.root.size == 5 * len(made) and not result.warnings


@POSIX_ONLY
def test_local_scan_non_utf8_name(tmp_path):
    raw = os.path.join(os.fsencode(tmp_path), b"bad-\xff-name")
    try:
        with open(raw, "wb") as f:
            f.write(b"123")
    except OSError:
        pytest.skip("filesystem rejects non-UTF-8 names")
    result = Scanner(get_platform()).scan(tmp_path)
    (node,) = result.root.children
    assert node.name == BAD and node.size == 3
    path = result.root_path.joinpath(*path_segments(node))
    assert os.fsencode(path) == raw  # the stored name still opens the real file
    back, _ = roundtrip(result)
    assert back.root.children[0].name == BAD


# ---- unplug mid-scan ------------------------------------------------------------------


def wide_tree(depth=4, fan=3, files=3):
    if depth == 0:
        return FakeDir({f"f{i}": FakeFile(100) for i in range(files)})
    kids = {f"d{i}": wide_tree(depth - 1, fan, files) for i in range(fan)}
    kids.update({f"f{i}": FakeFile(100) for i in range(files)})
    return FakeDir(kids)


@pytest.mark.parametrize("after", [0, 1, 2, 7, 30, 100])
def test_unplug_at_any_point_keeps_tree_consistent(after):
    plat = FakePlatform(wide_tree(), vanish_after=after)
    result = Scanner(plat, workers=3).scan(plat.root)
    assert result.volume_lost
    assert [w.kind for w in result.warnings].count("volume_lost") == 1
    for node in iter_nodes(result.root):
        if node.is_dir:
            assert node.size == sum(c.size for c in node.children)


def test_unplug_root_vanishes_before_listing():
    plat = FakePlatform(wide_tree())
    plat.gone = True
    result = Scanner(plat).scan(plat.root)
    assert not result.root.is_dir and result.files == 0
    assert result.warnings[0].kind == "vanished"


# ---- scan memory ---------------------------------------------------------------------


def test_scanner_bounds_folders_in_flight(monkeypatch):
    stats = {"now": 0, "max": 0}
    lock = threading.Lock()
    real_pool = scanner_mod.ThreadPoolExecutor

    class CountingPool(real_pool):
        def submit(self, fn, *args):
            with lock:
                stats["now"] += 1
                stats["max"] = max(stats["max"], stats["now"])
            fut = super().submit(fn, *args)

            def done(_):
                with lock:
                    stats["now"] -= 1

            fut.add_done_callback(done)
            return fut

    monkeypatch.setattr(scanner_mod, "ThreadPoolExecutor", CountingPool)
    plat = FakePlatform(wide_tree(depth=5, fan=4, files=1))
    result = Scanner(plat, workers=2).scan(plat.root)
    assert result.dirs > 1000
    assert stats["max"] <= 4  # workers * 2, not every folder at once


def test_scanner_shares_repeated_names():
    tree = FakeDir({f"d{i}": FakeDir({"same": FakeFile(1)}) for i in range(3)})
    root = fake_scan(tree).root
    a, b, c = (d.children[0].name for d in root.children)
    assert a is b is c


# ---- bench -----------------------------------------------------------------------------


def test_bench_tree_counts():
    tree = cli.bench_tree(1234)
    result = fake_scan(tree)
    assert result.files == 1234
    assert result.dirs < 1234 // 5
    assert any("\U0001f4e6" in n.name for n in iter_nodes(result.root))


def test_bench_main_reports():
    pytest.importorskip("psutil")  # CI installs core deps only
    out = io.StringIO()
    assert cli.bench_main(["--files", "3000"], out=out) == 0
    text = out.getvalue()
    assert "scan: 3000 files" in text and "cache: save" in text and "3000 files loaded" in text


def test_scan_warning_paths_survive_cache():
    result = ScanResult(Node("r", True), Path("r"), [ScanWarning("permission", (BAD, "x"), "m")])
    assert roundtrip(result)[0].warnings == result.warnings
