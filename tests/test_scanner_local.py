"""Real-filesystem scans, limited to pytest's tmp_path."""

import os
import sys

import pytest

from disk_usage_analyzer.__main__ import main
from disk_usage_analyzer.core.scanner import Scanner
from disk_usage_analyzer.platform import get_platform

IS_WINDOWS = sys.platform == "win32"


def scan(path, **kw):
    return Scanner(get_platform(), **kw).scan(path)


def names(node):
    return sorted(c.name for c in node.children)


def symlink_or_skip(target, link, is_dir=False):
    try:
        os.symlink(target, link, target_is_directory=is_dir)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted here")


def test_sizes(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"x" * 3000)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.bin").write_bytes(b"y" * 1234)
    r = scan(tmp_path)
    assert r.warnings == [] and r.files == 2 and r.dirs == 2
    assert r.root.size == 4234
    assert r.root.size_on_disk >= 0


def test_symlink_loop(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    (d / "f").write_bytes(b"z" * 10)
    symlink_or_skip(tmp_path, d / "loop", is_dir=True)
    symlink_or_skip(d / "f", tmp_path / "flink")
    r = scan(tmp_path)
    assert r.links_skipped == 2
    assert r.root.size == 10
    assert names(r.root) == ["d"]


def test_unicode_names(tmp_path):
    for n in ["résumé.txt", "日本語.md", "emoji-😀.bin"]:
        (tmp_path / n).write_bytes(b"1")
    r = scan(tmp_path)
    assert names(r.root) == sorted(["résumé.txt", "日本語.md", "emoji-😀.bin"])


@pytest.mark.skipif(IS_WINDOWS or (hasattr(os, "geteuid") and os.geteuid() == 0), reason="posix")
def test_permission_denied(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "secret").write_bytes(b"s")
    (tmp_path / "ok").write_bytes(b"ok")
    locked.chmod(0)
    try:
        r = scan(tmp_path)
    finally:
        locked.chmod(0o700)
    assert r.root.size == 2
    assert [(w.kind, w.path) for w in r.warnings] == [("permission", ("locked",))]


def test_hard_links_once(tmp_path):
    (tmp_path / "a").write_bytes(b"h" * 2000)
    os.link(tmp_path / "a", tmp_path / "b")
    r = scan(tmp_path)
    assert r.root.size == 2000 and r.hardlinks_deduped == 1


def test_missing_root(tmp_path):
    r = scan(tmp_path / "nope")
    assert r.warnings[0].kind == "vanished"


def test_cli_scan(tmp_path, capsys):
    (tmp_path / "big").write_bytes(b"b" * 5000)
    (tmp_path / "d").mkdir()
    for i in range(5):
        (tmp_path / "d" / f"f{i}").write_bytes(b"s" * (i + 1))
    (tmp_path / "e").mkdir()
    rc = main(["scan", str(tmp_path), "--top", "2", "--files", "1", "--quiet"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "5.0 KB  big" in out
    assert "Other (1 items)" in out
    assert "Largest files:" in out


def test_cli_missing_path(tmp_path, capsys):
    assert main(["scan", str(tmp_path / "nope"), "--quiet"]) == 1
    assert "Cannot scan" in capsys.readouterr().err
