from pathlib import Path

import pytest

from disk_usage_analyzer.core.models import Node
from disk_usage_analyzer.core.scanner import Scanner
from disk_usage_analyzer.core.tree import largest_files, path_segments, top_children
from disk_usage_analyzer.platform.fake import BLOCK, FakeDir, FakeFile, FakeLink, FakePlatform

ROOT = Path("fake-root")


def scan(tree: FakeDir, **kw):
    return Scanner(FakePlatform(tree, ROOT), **kw).scan(ROOT)


def child(node: Node, name: str) -> Node:
    return next(c for c in node.children if c.name == name)


def sample() -> FakeDir:
    return FakeDir(
        {
            "docs": FakeDir({"a.txt": FakeFile(100), "b.txt": FakeFile(5000, mtime=1700000000)}),
            "big.bin": FakeFile(1_000_000),
            "empty": FakeDir(),
        }
    )


def test_totals_and_aggregation():
    r = scan(sample())
    assert not r.cancelled and r.warnings == []
    assert (r.files, r.dirs) == (3, 2 + 1)  # docs, empty + root
    assert r.root.size == 1_005_100
    docs = child(r.root, "docs")
    assert docs.size == 5100
    assert docs.size_on_disk == BLOCK + BLOCK + 2 * BLOCK  # dir block + 1 + 2 blocks
    assert child(docs, "b.txt").mtime == 1700000000
    assert r.root.size_on_disk == sum(c.size_on_disk for c in r.root.children) + BLOCK


def test_symlinks_never_followed_even_loops():
    tree = FakeDir({"d": FakeDir({"loop": FakeLink(".."), "f": FakeFile(10)}), "up": FakeLink(".")})
    r = scan(tree)
    assert r.links_skipped == 2
    assert r.root.size == 10
    assert [c.name for c in child(r.root, "d").children] == ["f"]


def test_permission_error_is_warning_not_crash():
    tree = FakeDir({"locked": FakeDir(list_error=PermissionError), "ok": FakeFile(7)})
    r = scan(tree)
    assert r.root.size == 7
    assert [(w.kind, w.path) for w in r.warnings] == [("permission", ("locked",))]


def test_vanished_dir_and_entry():
    tree = FakeDir(
        {
            "gone": FakeDir(list_error=FileNotFoundError),
            "sub": FakeDir({"tmp": FakeFile(3, stat_error=FileNotFoundError)}),
            "io": FakeDir(list_error=OSError),
        }
    )
    r = scan(tree)
    got = sorted((w.kind, w.path) for w in r.warnings)
    assert got == [("error", ("io",)), ("vanished", ("gone",)), ("vanished", ("sub", "tmp"))]


def test_unicode_names():
    names = ["résumé.txt", "日本語", "emoji-😀.bin", "Ελληνικά"]
    tree = FakeDir(
        {names[0]: FakeFile(1), names[1]: FakeDir({names[2]: FakeFile(2)}), names[3]: FakeFile(3)}
    )
    r = scan(tree)
    assert r.root.size == 6
    f = child(child(r.root, names[1]), names[2])
    assert path_segments(f) == (names[1], names[2])


def test_hard_links_counted_once():
    tree = FakeDir({"a": FakeFile(500, file_id=9), "d": FakeDir({"b": FakeFile(500, file_id=9)})})
    r = scan(tree)
    assert r.root.size == 500
    assert r.hardlinks_deduped == 1
    assert r.files == 2


def test_hard_link_ids_are_per_filesystem():
    tree = FakeDir(
        {"a": FakeFile(500, file_id=9), "m": FakeDir({"b": FakeFile(500, file_id=9)}, dev=2)}
    )
    r = scan(tree, cross_mounts=True)
    assert r.root.size == 1000 and r.hardlinks_deduped == 0


def test_other_filesystem_skipped_by_default():
    tree = FakeDir({"mnt": FakeDir({"x": FakeFile(50)}, dev=2), "y": FakeFile(5)})
    r = scan(tree)
    assert r.root.size == 5
    assert child(r.root, "mnt").children == []
    assert [(w.kind, w.path) for w in r.warnings] == [("other_filesystem", ("mnt",))]
    r2 = scan(tree, cross_mounts=True)
    assert r2.root.size == 55 and r2.warnings == []


def test_root_missing_or_file():
    r = Scanner(FakePlatform(FakeDir(), ROOT)).scan(ROOT / "nope")
    assert r.warnings[0].kind == "vanished" and not r.root.is_dir
    r = Scanner(FakePlatform(FakeDir({"f": FakeFile(42)}), ROOT)).scan(ROOT / "f")
    assert r.root.size == 42 and r.files == 1


def deep_tree(depth: int, width: int) -> FakeDir:
    tree = FakeDir({f"f{i}": FakeFile(1) for i in range(width)})
    node = tree
    for d in range(depth):
        nxt = FakeDir({f"f{i}": FakeFile(1) for i in range(width)})
        node.children[f"d{d}"] = nxt
        node = nxt
    return tree


def test_deep_tree_is_iterative():
    r = scan(deep_tree(depth=1200, width=1))  # deeper than the recursion limit
    assert r.files == 1201 and r.root.size == 1201


def test_progress_reported():
    seen = []
    scan(deep_tree(50, 3), progress=seen.append, progress_interval=0)
    assert seen and seen[-1].files == 153 and seen[-1].size == 153
    assert all(a.files <= b.files for a, b in zip(seen, seen[1:], strict=False))


def test_cancel_returns_partial_result():
    holder: list[Scanner] = []
    scanner = Scanner(
        FakePlatform(deep_tree(500, 2), ROOT),
        progress=lambda p: holder[0].cancel(),
        progress_interval=0,
    )
    holder.append(scanner)
    r = scanner.scan(ROOT)
    assert r.cancelled and scanner.cancelled
    assert 0 < r.files < 1002


def test_cancel_before_start():
    scanner = Scanner(FakePlatform(sample(), ROOT))
    scanner.cancel()
    r = scanner.scan(ROOT)
    assert r.cancelled and r.files == 0


@pytest.mark.parametrize("workers", [1, 8])
def test_worker_count_does_not_change_result(workers):
    r = scan(deep_tree(20, 5), workers=workers)
    assert r.files == 105


def test_top_children_rollup():
    tree = FakeDir({f"f{i}": FakeFile(i + 1) for i in range(10)})
    r = scan(tree)
    top, rest = top_children(r.root, 3)
    assert [c.name for c in top] == ["f9", "f8", "f7"]
    assert rest.count == 7 and rest.size == sum(range(1, 8))
    top, rest = top_children(r.root, 20)
    assert len(top) == 10 and rest is None


def test_largest_files():
    r = scan(sample())
    assert [f.name for f in largest_files(r.root, 2)] == ["big.bin", "b.txt"]
